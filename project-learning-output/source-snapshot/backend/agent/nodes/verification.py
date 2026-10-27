"""在生成报告前检查结果。能够修正的问题会交回模型继续处理。"""
import re
from backend.agent.nodes.tools import validate_arguments, feedback_messages
from backend.reporting.verification import verify_report_inputs, select_report_artifacts
from backend.repositories.analysis_tasks import emit_event
from backend.tools.customer_metrics import customer_repeat_requested
from backend.reporting.requirements import requested_columns, python_requested, chart_requested, chart_forbidden


async def verify(state: dict) -> dict:
    """检查 finish 参数、用户要求和实际结果。检查通过才允许生成报告，失败则提供具体错误。"""
    try:
        args = state['action']['arguments']
        validate_arguments('finish', args)
        artifacts = select_report_artifacts(state['artifacts'], args.get('result_refs'))
        question = state['question'] + '\n' + state.get('clarification_answer', '')
        columns = requested_columns(question)
        if chart_forbidden(question) and any(item['kind'] == 'chart' for item in artifacts):
            raise ValueError('用户明确不要图表，最终结果不能选择图表；请只选择符合要求的表格')
        if chart_requested(question) and not any(item['kind'] == 'chart' for item in artifacts):
            raise ValueError('用户要求图表，但尚未生成图表；请对真实结果表绘图后再完成')
        # finish 只是模型提出结束分析。程序仍要确认所需列、Python 计算和专用指标已经实际生成。
        if columns and not any(columns <= set(item.get('columns', [])) for item in artifacts if item['kind'] == 'table'):
            raise ValueError('尚未完成用户要求的全部结果列：' + ', '.join(sorted(columns)) + '；请实际计算并放入同一结果表，不能仅删去说明后结束。')
        if python_requested(question) and not any(item.get('python') for item in artifacts):
            raise ValueError('用户明确要求 Python 计算，不能用 SQL 替代后直接结束；请对已返回的 artifact_ref 调用 run_python 并保存结果。')
        if customer_repeat_requested(question) and not any(item.get('metric_id') == 'customer_repeat' for item in artifacts):
            raise ValueError('复购报告缺少权威客户粒度计算；请调用 customer_repeat_metrics，它会先按 customer_unique_id 合并全部订单')
        verify_report_inputs(state['task_id'], artifacts, args['notes'], args['title'],
                             question, state['metrics']['valid_status'])
        # verified 决定后续流程是否进入报告生成节点。只有程序检查通过，才允许生成报告。
        await emit_event(state['task_id'], 'verified', {'message': '结果文件与报告依据检查通过'})
        return {'action': {**state['action'], 'verified': True}, 'report_artifacts': artifacts}
    except ValueError as error:
        # 把能够修正的检查错误作为工具反馈交给模型，保留已有结果，让模型补算或修改说明。
        await emit_event(state['task_id'], 'verification_error', {'message': str(error)})
        failures = state.get('verification_failures', 0) + 1
        if failures >= 3:
            if re.match(r'^(?:title|notes\[\d+\]) 含无依据数字 ', str(error)):
                # 只有说明中的数字缺少依据时才可改为仅交付计算结果。缺列、缺工具、范围错误或图表不完整仍会失败。
                safe_args = {'title': '数据分析结果报告', 'notes': [
                    '模型说明连续未通过数值依据核验。本报告仅交付工具实际计算结果与图表。']}
                verify_report_inputs(state['task_id'], artifacts, safe_args['notes'], safe_args['title'],
                                     question, state['metrics']['valid_status'])
                await emit_event(state['task_id'], 'report_fallback', {'message': safe_args['notes'][0], 'reason': str(error)})
                return {'action': {**state['action'], 'arguments': safe_args, 'verified': True},
                        'verification_failures': failures, 'report_warning': safe_args['notes'][0], 'report_artifacts': artifacts}
            raise ValueError('报告依据校验连续修正三次仍失败：' + str(error)) from None
        return {'action': {**state['action'], 'verified': False},
                'verification_failures': failures,
                'messages': feedback_messages(state, {'error': str(error)})}
