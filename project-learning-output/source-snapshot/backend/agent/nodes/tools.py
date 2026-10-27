"""根据模型选择执行工具，并把结果交回模型。用户身份、数据集和任务编号由服务端确定。"""
import asyncio
import json
from sqlalchemy.exc import DBAPIError
from typing import TypedDict
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.messages.utils import convert_to_openai_messages
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from backend.agent.tool_registry import BUSINESS_TOOLS, TOOL_BY_NAME
from backend.core.config import get_settings
from backend.reporting.requirements import chart_forbidden
from backend.tools.dataset_schema import describe_schema
from backend.tools.sql import search_data
from backend.tools.python import analyze_table
from backend.tools.chart import create_chart
from backend.tools.customer_metrics import customer_repeat_metrics
from backend.tools.sales_metrics import sales_metrics
from backend.memory.service import resolve_personalization
from backend.repositories.analysis_tasks import check_cancelled, emit_event, register_artifact


def validate_arguments(name: str, arguments: dict) -> None:
    """复用工具的 Pydantic schema 检查控制动作和兼容入口，不再手工维护参数类型表。"""
    definition = TOOL_BY_NAME.get(name)
    if definition is None or not isinstance(arguments, dict):
        raise ValueError('工具名称或参数无效')
    definition.args_schema.model_validate(arguments, strict=True)


def feedback_messages(state: dict, body: dict) -> list[dict]:
    """把工具结果整理成长度有限的消息，并标明它对应哪一次模型工具调用。"""
    if state['mode'] == 'demo':
        return state['messages']
    message = ToolMessage(content=feedback_content(body), tool_call_id=state['action']['call_id'])
    return [*state['messages'], convert_to_openai_messages(message)]


def feedback_content(body: dict) -> str:
    """生成有长度上限的完整 JSON，SQL、代码和服务器路径不重复放入模型反馈。"""
    visible = {key: value for key, value in body.items() if key not in ('sql', 'effective_sql', 'python', 'filename', 'task_id', 'dataset_id')}
    content = json.dumps(visible, ensure_ascii=False)
    if len(content) > 6000:
        # 结果太长时重新生成较小的 JSON。不能直接截断字符串，否则模型可能无法解析结果和文件引用。
        compact = {key:body[key] for key in ('id','artifact_ref','kind','label','columns','row_count','source_id','metric_id') if key in body}
        compact['notice'] = '结果摘要较大，请使用引用或进一步聚合；完整结果已保存。'
        if body.get('chart'):
            compact['chart'] = {key:body['chart'][key] for key in ('id','kind','source_id','source_row_count','plotted_row_count') if key in body['chart']}
        content = json.dumps(compact, ensure_ascii=False)
    # tool_call_id 必须与 assistant 消息里的调用 ID 一致，模型才能识别这条结果属于哪次调用。
    return content


def resolve_table_reference(state: dict, reference: str) -> str:
    """把本任务中的 table_1 等短名称或真实 UUID 转为表格 ID。找不到时，报错并列出可用的表格名称。"""
    tables = [item for item in state.get('artifacts', []) if item['kind'] == 'table']
    for index, item in enumerate(tables, 1):
        if reference in (item['id'], item.get('artifact_ref', f'table_{index}')):
            return item['id']
    # 只在当前任务的表格中查找引用，不允许模型使用其他任务的表格或自行指定文件路径。
    available = ', '.join(item.get('artifact_ref', f'table_{index}') for index, item in enumerate(tables, 1)) or '无表格，请先查询'
    raise ValueError('表格引用无效，不能编造 UUID。当前任务可用 artifact_ref：' + available)


async def publish_result(state: dict, artifacts: list[dict], result: dict) -> dict:
    """为表格添加便于模型使用的短名称，确认任务未取消后登记结果，并把结果加入产物列表。"""
    await check_cancelled(state['task_id'])
    result = await register_artifact(state['task_id'], result)
    artifacts.append(result)
    return result


async def emit_tool_event(state: dict, kind: str, payload: dict) -> None:
    """给工具事件添加调用 ID，让并发同名工具的开始与完成时间仍能一一对应。"""
    await emit_event(state['task_id'], kind, {**payload, 'call_id': state['action'].get('call_id')})


async def execute_business(state: dict) -> dict:
    """执行业务约束与产物登记；参数解析和工具分发由 ToolNode 处理。"""
    await check_cancelled(state['task_id'])
    name, args = state['action']['name'], state['action']['arguments']
    artifacts = list(state.get('artifacts', []))
    completion_emitted = False
    partial = None
    try:
        question = state.get('question', '') + '\n' + state.get('clarification_answer', '')
        if chart_forbidden(question) and (name == 'render_chart' or any(key.startswith('chart_') for key in args)):
            raise ValueError('用户已明确不需要绘图，请仅交付计算结果')
        # 把用户补充的要求也纳入本次偏好判断，避免旧偏好覆盖用户刚指定的图表类型。
        selected = resolve_personalization(state.get('question', '') + '\n' + state.get('clarification_answer',''), state.get('personalization', {}))
        if name == 'render_chart' and selected.get('chart_kind'):
            args = {**args, 'kind':selected['chart_kind']}
        if name == 'execute_sql' and 'chart_x' in args and selected.get('chart_kind'):
            args = {**args, 'chart_kind':selected['chart_kind']}
        if name in ('run_python', 'render_chart'):
            args = {**args, 'artifact_id': resolve_table_reference(state, args['artifact_id'])}
        await emit_tool_event(state, 'tool_started', {'tool': name, 'arguments': args,
                          'requested_arguments':state['action']['arguments']})
        if name == 'get_dataset_schema':
            body = describe_schema(state['metrics'])
            update = {'schema_loaded': True}
        else:
            if name == 'execute_sql':
                # 查询使用图状态中的任务 ID、数据集 ID 和有效订单状态，模型只能提供查询内容。
                result = await search_data(state['task_id'], state['dataset_id'], args['query'], state['metrics']['valid_status'])
            elif name == 'customer_repeat_metrics':
                result = await customer_repeat_metrics(state['task_id'], state['dataset_id'], state['metrics']['valid_status'], **args)
            elif name == 'sales_metrics':
                result = await sales_metrics(state['task_id'], state['dataset_id'], state['metrics']['valid_status'], **{key:value for key,value in args.items() if not key.startswith('chart_')})
            elif name == 'run_python':
                result = await analyze_table(state['task_id'], args['artifact_id'], args['code'])
            elif name == 'render_chart':
                result = await asyncio.to_thread(create_chart, state['task_id'], currency=state['metrics']['currency'], **args)
            else:
                raise ValueError('该动作不是执行工具')
            result = await publish_result(state, artifacts, result)
            body, update = result, {}
            if name in ('execute_sql', 'sales_metrics') and 'chart_x' in args:
                # 模型可在一次调用中同时请求查询和绘图。程序先登记查询结果，再顺序绘图，减少一次模型请求。
                partial = result
                await emit_tool_event(state, 'tool_completed', {'tool': name, 'result': result})
                name = 'render_chart'
                chart_args = {'artifact_id': result['id'], 'x': args['chart_x'], 'y': args['chart_y'],
                              'title': args['chart_title'], 'kind': args.get('chart_kind', 'bar')}
                await emit_tool_event(state, 'tool_started', {'tool': name, 'arguments': chart_args, 'combined_with_sql': True})
                chart = await asyncio.to_thread(create_chart, state['task_id'], currency=state['metrics']['currency'], **chart_args)
                chart = await publish_result(state, artifacts, chart)
                await emit_tool_event(state, 'tool_completed', {'tool': name, 'result': chart, 'combined_with_sql': True})
                body = {**result, 'chart': chart}
                completion_emitted = True
        if not completion_emitted:
            await emit_tool_event(state, 'tool_completed', {'tool': name, 'result': body})
        return {**update, 'artifacts': artifacts, 'body': body}
    except InterruptedError:
        # 用户取消任务时直接向上抛出异常，让 Worker 停止。不能把取消当作普通工具错误交给模型重试。
        raise
    except Exception as error:
        message = str(error)[:800] if isinstance(error, (ValueError, SyntaxError)) else '工具执行失败：' + type(error).__name__
        if isinstance(error, DBAPIError):
            code = getattr(error.orig, 'sqlstate', None)
            if code in ('42702', '42703', '42P01', '42883', '42601', '42804', '42803'):
                # 只反馈允许公开的 SQL 字段或语法错误，帮助模型修正查询，不把所有数据库异常内容直接发给模型。
                message = str(error.orig).splitlines()[0][:500]
            elif code == '57014':
                message = 'SQL 查询超时，请缩小查询范围或减少关联。'
        await emit_tool_event(state, 'tool_error', {'tool': name, 'message': message})
        feedback = {'error': message}
        if partial:
            # 如果查询成功但绘图失败，就反馈已保存的表格引用。模型可以重新绘图，不必再次查询相同数据。
            feedback['table'] = {'id': partial['id'], 'artifact_ref': partial['artifact_ref'], 'columns': partial['columns'], 'preview': partial['preview']}
        return {'body': feedback, 'artifacts': artifacts}


class ToolExecutionState(TypedDict):
    """描述无 checkpoint 的工具执行子图，业务上下文仅由服务端注入。"""
    messages: list
    backend_state: dict


def tool_error_feedback(error: Exception) -> str:
    """向模型返回可修正的简明错误，取消异常继续向上传播以停止整批执行。"""
    if isinstance(error, InterruptedError):
        raise error
    return json.dumps({'error': str(error)[:800] if isinstance(error, ValueError) else '工具参数或执行失败：' + type(error).__name__}, ensure_ascii=False)


def build_tool_runner():
    """创建标准 ToolNode 子图，让框架提供 ToolRuntime、参数校验和每个调用的 ToolMessage。"""
    builder = StateGraph(ToolExecutionState)
    builder.add_node('tools', ToolNode(BUSINESS_TOOLS, handle_tool_errors=tool_error_feedback))
    builder.add_edge(START, 'tools')
    builder.add_edge('tools', END)
    # 明确关闭子图 checkpoint，避免继承父图的 PostgreSQL saver 并尝试保存 Semaphore/Task。
    # 持久进度由外层业务图保存；此处只执行当前批次，运行时限流对象不进入数据库。
    return builder.compile(checkpointer=False)


TOOL_RUNNER = build_tool_runner()


async def execute_tool(state: dict) -> dict:
    """完整处理一轮调用：独立只读操作受限并发，依赖已有表格的 Python 与绘图串行执行。"""
    await check_cancelled(state['task_id'])
    calls = [{**call, 'call_id': call.get('call_id') or f"legacy_{state.get('steps', 0)}_{index}"}
             for index, call in enumerate(state['action'].get('calls') or [state['action']])]
    settings = get_settings()
    names = [call['name'] for call in calls]
    # finish/澄清不能与计算混批，否则工具尚未完成时就可能暂停或提前交付。
    if len(calls) > settings.max_tools_per_round or any(name in ('finish', 'ask_clarification') for name in names):
        reason = '一轮工具数量超过上限' if len(calls) > settings.max_tools_per_round else 'finish 和人工澄清必须单独调用；本批次未执行，请先完成业务工具'
        await emit_event(state['task_id'], 'tool_error', {'tool': 'tool_batch', 'message': reason})
        messages = [convert_to_openai_messages(ToolMessage(content=json.dumps({'error': reason}, ensure_ascii=False), tool_call_id=call['call_id'])) for call in calls]
        return {'messages': [*state['messages'], *messages]}
    independent = {'get_dataset_schema', 'execute_sql', 'customer_repeat_metrics', 'sales_metrics'}
    parallel = [call for call in calls if call['name'] in independent or call['name'] not in TOOL_BY_NAME]
    dependent = [call for call in calls if call['name'] not in independent and call['name'] in TOOL_BY_NAME]
    groups = ([parallel] if parallel else []) + [[call] for call in dependent]
    current = {**state, 'artifacts': list(state.get('artifacts', [])), '_tool_semaphore': asyncio.Semaphore(settings.tool_concurrency), '_tool_tasks': set()}
    results = {}
    if len(calls) > 1:
        await emit_event(state['task_id'], 'tool_batch_started', {'tools': names, 'count': len(calls), 'concurrency': settings.tool_concurrency})
    for group in groups:
        await check_cancelled(state['task_id'])
        standard_calls = [{'name': call['name'], 'args': call['arguments'], 'id': call['call_id'], 'type': 'tool_call'} for call in group]
        try:
            result = await TOOL_RUNNER.ainvoke({'messages': [AIMessage(content='', tool_calls=standard_calls)], 'backend_state': current})
        except BaseException:
            # ToolNode 内部并发执行。取消或失败向外传播前，停止仍执行的兄弟任务并等待清理，防止后台继续写产物。
            pending = list(current['_tool_tasks'])
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            raise
        for message in result['messages']:
            results[message.tool_call_id] = convert_to_openai_messages(message)
            detail = message.artifact or {}
            known = {item['id'] for item in current['artifacts']}
            current['artifacts'] += [item for item in detail.get('artifacts', []) if item['id'] not in known]
            if detail.get('schema_loaded'):
                current['schema_loaded'] = True
            if message.status == 'error':
                await emit_event(state['task_id'], 'tool_error', {'tool': message.name, 'message': message.content})
    await check_cancelled(state['task_id'])
    if len(calls) > 1:
        await emit_event(state['task_id'], 'tool_batch_completed', {'count': len(calls)})
    # 按模型原调用顺序保存反馈，执行完成顺序可以不同，但所有调用都有对应结果。
    return {'messages': state['messages'] if state['mode'] == 'demo' else [*state['messages'], *[results[call['call_id']] for call in calls]],
            'artifacts': current['artifacts'], 'schema_loaded': current.get('schema_loaded', False)}
