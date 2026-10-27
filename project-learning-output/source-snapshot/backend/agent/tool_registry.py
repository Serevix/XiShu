"""用 LangChain 工具定义统一参数协议，业务执行仍遵守任务归属、数据库权限和沙箱限制。"""
from typing import Literal
from langchain_core.tools import tool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.prebuilt import ToolRuntime


async def invoke_business(runtime: ToolRuntime, name: str, arguments: dict):
    """在本批次并发限额内执行授权业务工具，分别返回模型可见内容与内部产物。"""
    from backend.agent.nodes.tools import execute_business, feedback_content
    state = runtime.state['backend_state']
    action = {'name': name, 'arguments': arguments, 'call_id': runtime.tool_call_id}
    import asyncio
    task = asyncio.current_task()
    state['_tool_tasks'].add(task)
    try:
        async with state['_tool_semaphore']:
            result = await execute_business({**state, 'action': action})
    finally:
        state['_tool_tasks'].discard(task)
    return feedback_content(result['body']), result


@tool(response_format='content_and_artifact')
async def get_dataset_schema(runtime: ToolRuntime):
    """获取当前授权数据集的字段、表间关联和已经确认的统计口径。"""
    return await invoke_business(runtime, 'get_dataset_schema', {})


@tool(response_format='content_and_artifact')
async def execute_sql(query: str, runtime: ToolRuntime, chart_x: str | None = None,
                      chart_y: str | None = None, chart_title: str | None = None,
                      chart_kind: Literal['bar', 'line'] | None = None):
    """执行受限 PostgreSQL SELECT。数据集和有效状态由服务端限定。可同时指定 chart_x、chart_y、chart_title 顺序生成完整图表。"""
    args = {'query': query, 'chart_x': chart_x, 'chart_y': chart_y,
            'chart_title': chart_title, 'chart_kind': chart_kind}
    if any(value is not None for key, value in args.items() if key.startswith('chart_')):
        if not all(args[key] is not None for key in ('chart_x', 'chart_y', 'chart_title')):
            raise ValueError('联合绘图需要 chart_x、chart_y、chart_title')
    return await invoke_business(runtime, 'execute_sql', {key: value for key, value in args.items() if value is not None})


@tool(response_format='content_and_artifact')
async def customer_repeat_metrics(runtime: ToolRuntime, start_date: str | None = None,
                                   end_date: str | None = None):
    """按 customer_unique_id 合并有效订单，计算独立客户、复购客户和复购率。开始日期包含，结束日期不包含。"""
    args = {key: value for key, value in {'start_date': start_date, 'end_date': end_date}.items() if value is not None}
    return await invoke_business(runtime, 'customer_repeat_metrics', args)


@tool(response_format='content_and_artifact')
async def sales_metrics(runtime: ToolRuntime, start_date: str | None = None,
                         end_date: str | None = None, group_by: Literal['total', 'month'] = 'total',
                         chart_x: str | None = None, chart_y: str | None = None,
                         chart_title: str | None = None, chart_kind: Literal['bar', 'line'] | None = None):
    """固定口径销售指标：order_count/item_amount/freight_amount/total_amount/aov；月度返回差值和两位小数增长率。可用 chart_x/chart_y/chart_title 自动绘图，减少模型轮次。"""
    args = {'group_by': group_by, 'start_date': start_date, 'end_date': end_date,
            'chart_x': chart_x, 'chart_y': chart_y, 'chart_title': chart_title, 'chart_kind': chart_kind}
    if any(value is not None for key, value in args.items() if key.startswith('chart_')):
        if not all(args[key] is not None for key in ('chart_x', 'chart_y', 'chart_title')):
            raise ValueError('联合绘图需要 chart_x、chart_y、chart_title')
    return await invoke_business(runtime, 'sales_metrics', {key: value for key, value in args.items() if value is not None})


@tool(response_format='content_and_artifact')
async def run_python(artifact_id: str, code: str, runtime: ToolRuntime):
    """对本任务已返回的表格引用执行受限 Python。df/pd/np/Decimal 已提供，结果赋值 result；不导入模块、不读文件。"""
    return await invoke_business(runtime, 'run_python', {'artifact_id': artifact_id, 'code': code})


@tool(response_format='content_and_artifact')
async def render_chart(artifact_id: str, x: str, y: str, title: str, runtime: ToolRuntime,
                        kind: Literal['bar', 'line'] = 'bar'):
    """将本任务已返回表格的全部行绘制为图表。依赖的查询先完成，不自动截取 Top N。"""
    return await invoke_business(runtime, 'render_chart', {'artifact_id': artifact_id, 'x': x, 'y': y, 'title': title, 'kind': kind})


@tool
def ask_clarification(question: str):
    """缺少必要业务信息时询问用户，单独调用；由图中的人工澄清节点暂停和恢复。"""
    raise RuntimeError('人工澄清必须由专用节点执行')


@tool
def finish(title: str, notes: list[str], result_refs: list[str] | None = None):
    """单独结束分析；result_refs 指定最终表格别名和图表 ID，不选择分页预览和重复中间表。说明默认不超过三条，只用已有数字，不主动增加未要求的派生计算。"""
    raise RuntimeError('报告完成必须由校验节点执行')


BUSINESS_TOOLS = [get_dataset_schema, execute_sql, customer_repeat_metrics, sales_metrics, run_python, render_chart]
MODEL_TOOLS = [*BUSINESS_TOOLS, ask_clarification, finish]
for item in MODEL_TOOLS:
    # 框架推导默认可能忽略额外字段。显式关闭这一行为，避免未知参数被悄悄接受。
    item.args_schema.model_config.update(extra='forbid', strict=True)
    item.args_schema.model_rebuild(force=True)
TOOL_BY_NAME = {item.name: item for item in MODEL_TOOLS}
# 预算检查也使用框架生成的相同 schema，避免模型协议与执行工具各维护一份参数定义。
TOOLS = [convert_to_openai_tool(item) for item in MODEL_TOOLS]
