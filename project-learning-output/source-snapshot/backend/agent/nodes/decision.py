"""让模型选择下一步操作，并检查任务是否取消、是否达到最大步骤数。"""
from backend.agent.model import model_action, demo_action
from backend.core.config import get_settings
from backend.repositories.analysis_tasks import check_cancelled, emit_event
from backend.repositories.analysis_tasks import update_task


async def decide(state: dict) -> dict:
    """选择下一步操作并累计模型用量。达到步骤上限时停止，防止分析一直循环。"""
    await check_cancelled(state['task_id'])
    # 每轮决策都计入步骤数，即使上一轮工具失败也不例外，避免模型在同一个错误上无限重试。
    if state.get('steps', 0) >= get_settings().max_agent_steps:
        raise ValueError('达到最大分析步骤，请缩小问题范围')
    messages = state['messages']
    usage = dict(state.get('usage', {}))
    if state['mode'] == 'demo':
        # 演示模式按预设规则选择动作，真实模式通过聊天模型选择工具。
        action = demo_action(state)
    else:
        await emit_event(state['task_id'], 'model_started', {'step': state.get('steps', 0) + 1})
        action, messages, increment = await model_action(state)
        for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
            usage[key] = usage.get(key, 0) + int(increment.get(key, 0))
        await update_task(state['task_id'], usage=usage)
        await emit_event(state['task_id'], 'model_completed', {'seconds': increment.get('model_seconds'),
                         'http_attempts': increment.get('http_attempts'),
                         'tokens': {key: increment.get(key, 0) for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')}})
    await emit_event(state['task_id'], 'decision', {'tool': action['name'], 'tools': [call['name'] for call in action.get('calls', [action])], 'step': state.get('steps', 0) + 1})
    # 只返回本节点修改的状态字段，LangGraph 会把它们合并到现有状态中。
    return {'action': action, 'messages': messages, 'usage': usage, 'steps': state.get('steps', 0) + 1}
