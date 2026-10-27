"""每轮决策前整理摘要和近期消息，控制请求长度。执行进度中仍保留原始消息。"""
from backend.context.manager import prepare_context
from backend.repositories.analysis_tasks import emit_event


async def manage_context(state: dict) -> dict:
    """整理本轮上下文，并记录摘要成功或失败的情况。不会删除数据库或图状态中的原文。"""
    from backend.agent.model import TOOLS
    update = await prepare_context(state, TOOLS)
    if update.get('summary_cursor', 0) > state.get('summary_cursor', 0):
        await emit_event(state['task_id'], 'context_summarized', {'cursor':update['summary_cursor'], 'usage':update.get('summary_usage', {}), 'message':'已总结旧消息，保留完整历史与近期工具链'})
    if update.get('summary_error'):
        await emit_event(state['task_id'], 'context_fallback', {'message':update['summary_error'], 'cursor_unchanged':True})
    return update
