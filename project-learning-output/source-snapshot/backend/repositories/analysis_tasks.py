"""读取和更新分析任务，保存步骤事件及结果文件信息。"""
from fastapi import HTTPException
from sqlalchemy import select
from backend.core.database import Session
from backend.models.analysis_task import AnalysisTask
from backend.models.base import utcnow


async def owned_task(session, task_id: str, user_id: str) -> AnalysisTask:
    """查找属于当前用户的任务。任务不存在或属于其他用户时，都返回任务不存在错误。"""
    obj = await session.scalar(select(AnalysisTask).where(AnalysisTask.id == task_id, AnalysisTask.user_id == user_id))
    if obj is None:
        raise HTTPException(404, '任务不存在')
    return obj


async def update_task(task_id: str, *, event: tuple[str, dict] | None = None, **changes) -> bool:
    """更新任务状态，并按需写入一条事件，两项修改在同一个事务中提交。
    
    更新前先锁住任务记录，避免取消操作与状态更新同时修改它。
    返回 True 表示修改已提交；任务不存在或已被取消时返回 False，不再更新状态或写入事件。
    """
    async with Session.begin() as session:
        task = await session.get(AnalysisTask, task_id, with_for_update=True)
        # 这里和取消接口都先锁住同一条任务记录。如果取消操作已经提交，就不能再把任务更新为成功。
        if task is None or task.status == 'cancelled':
            return False
        for key, value in changes.items():
            setattr(task, key, value)
        if event:
            kind, payload = event
            # 任务状态和完成事件一起提交，防止页面看到任务已成功，却还读不到完成事件。
            # 给 JSONB 字段赋一个新列表，让 ORM 识别内容变化。直接对原列表 append 可能不会触发数据库更新。
            task.events = [*task.events, {'seq': len(task.events) + 1, 'type': kind,
                           'time': utcnow().isoformat(), 'payload': payload}]
        task.updated_at = utcnow()
    return True


async def emit_event(task_id: str, kind: str, payload: dict) -> None:
    """把步骤事件保存到 PostgreSQL，前端随后通过 SSE 按事件序号读取。"""
    async with Session.begin() as session:
        task = await session.get(AnalysisTask, task_id, with_for_update=True)
        if task is None:
            return
        # 先锁住任务记录再分配事件序号，避免并发写入产生相同序号。浏览器用序号补读已保存的事件。
        events = list(task.events)
        events.append({'seq': len(events) + 1, 'type': kind,
                       'time': utcnow().isoformat(), 'payload': payload})
        task.events = events
        task.updated_at = utcnow()


async def register_artifact(task_id: str, artifact: dict) -> dict:
    """锁住任务后登记产物并分配唯一表格别名；已取消任务拒绝登记，同 ID 重投返回原记录。"""
    async with Session.begin() as session:
        task = await session.get(AnalysisTask, task_id, with_for_update=True)
        if task is None or task.status == 'cancelled':
            raise InterruptedError('任务已取消')
        existing = next((item for item in task.artifacts if item['id'] == artifact['id']), None)
        if existing:
            return existing
        if artifact['kind'] == 'table':
            # 并发工具不能各自按旧快照计算 table_1。分配与登记放在同一个行锁事务中。
            artifact = {**artifact, 'artifact_ref': f"table_{sum(item['kind'] == 'table' for item in task.artifacts) + 1}"}
        task.artifacts = [*task.artifacts, artifact]
        return artifact


async def check_cancelled(task_id: str) -> None:
    """检查任务是否被取消。已取消或任务不存在时抛出异常，让调用方停止后续分析。"""
    async with Session() as session:
        task = await session.get(AnalysisTask, task_id)
        if task is None or task.status == 'cancelled':
            raise InterruptedError('任务已取消')


async def mark_report_selection(task_id: str, selected_ids: set[str]) -> None:
    """在任务行锁中标记最终交付与中间结果，保留全部来源供追溯，不删除文件。"""
    async with Session.begin() as session:
        task = await session.get(AnalysisTask, task_id, with_for_update=True)
        if task is None or task.status == 'cancelled':
            raise InterruptedError('任务已取消')
        task.artifacts = [{**item, 'delivery_role': 'final' if item['id'] in selected_ids or item['kind']=='report' else 'intermediate'} for item in task.artifacts]
