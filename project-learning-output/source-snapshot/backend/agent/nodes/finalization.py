"""生成报告文件，并在任务记录中保存报告的引用信息。"""
import asyncio
from backend.reporting.builder import build_report
from backend.repositories.analysis_tasks import register_artifact, emit_event, check_cancelled, mark_report_selection


async def finalize(state: dict) -> dict:
    """确认任务没有取消后生成报告，登记报告文件并返回其信息。"""
    await check_cancelled(state['task_id'])
    # 报告构建是同步操作，因此放到线程中执行，让异步事件循环仍能处理其他等待。
    selected = state.get('report_artifacts', state['artifacts'])
    report = await asyncio.to_thread(build_report, {**state, 'artifacts': selected})
    await register_artifact(state['task_id'], report)
    await mark_report_selection(state['task_id'], {item['id'] for item in selected})
    await emit_event(state['task_id'], 'report_ready', {'artifact_id': report['id']})
    # report_ready 表示报告文件已经生成。Worker 还要保存会话原文，再一起提交成功状态和完成事件。
    return {'report': report}
