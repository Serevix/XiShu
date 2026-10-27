"""提供任务管理和步骤事件接口。浏览器断开事件连接时，后台分析仍会继续。"""
import asyncio
import json
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from backend.core.database import get_session, Session
from backend.api.dependencies import current_user
from backend.models.analysis_task import AnalysisTask
from backend.repositories.analysis_tasks import owned_task
from backend.schemas.analysis_tasks import TaskRequest, ResumeRequest
from backend.services.analysis_tasks import create_task, serialize_task, dispatch
from backend.models.base import utcnow

router = APIRouter(prefix='/api/analysis-tasks', tags=['分析任务'])


@router.get('')
async def list_tasks(user=Depends(current_user), session=Depends(get_session)):
    """返回当前用户最近的任务。需要完整事件时再读取任务详情。"""
    rows = await session.scalars(select(AnalysisTask).where(AnalysisTask.user_id == user.id).order_by(AnalysisTask.created_at.desc()).limit(50))
    return [serialize_task(row) for row in rows]


@router.post('', status_code=201)
async def submit_task(payload: TaskRequest, user=Depends(current_user), session=Depends(get_session)):
    """先把任务保存到数据库，再向队列发送任务消息，返回创建好的任务信息。"""
    task = await create_task(session, user.id, payload)
    # 接口返回的是已创建的任务，不等待分析结束。发送队列失败时保留任务记录，由后台稍后重试。
    return {**serialize_task(task), 'dispatched': dispatch('analytics.analyze', task.id)}


@router.get('/{task_id}')
async def get_task(task_id: str, user=Depends(current_user), session=Depends(get_session)):
    """读取任务状态、事件和结果信息，让页面刷新后仍能查看之前的分析。"""
    return serialize_task(await owned_task(session, task_id, user.id), include_events=True)


@router.post('/{task_id}/resume')
async def resume_task(task_id: str, payload: ResumeRequest, user=Depends(current_user), session=Depends(get_session)):
    """保存用户补充的答案并重新排队。只有等待输入的任务才能恢复，重复提交会被拒绝。"""
    task = await owned_task(session, task_id, user.id)
    await session.refresh(task, with_for_update=True)
    if task.status != 'waiting_input':
        raise HTTPException(409, '任务当前不在等待输入')
    # 先把答案保存到数据库，再发任务消息。Worker 使用原 thread_id 的执行进度继续分析，不新建任务。
    task.resume_payload, task.status = payload.answer, 'queued'
    await session.commit()
    return {'status': task.status, 'dispatched': dispatch('analytics.analyze', task.id)}


@router.post('/{task_id}/cancel')
async def cancel_task(task_id: str, user=Depends(current_user), session=Depends(get_session)):
    """把任务状态改为已取消，并写入取消事件。执行器会在下一次步骤检查时停止后续操作。"""
    task = await owned_task(session, task_id, user.id)
    await session.refresh(task, with_for_update=True)
    if task.status in ('succeeded', 'failed', 'cancelled'):
        raise HTTPException(409, '任务已结束')
    task.status = 'cancelled'
    # 取消状态和取消事件一起提交。即使任务还在排队、没有 Worker 执行，页面也能收到取消结果。
    task.updated_at = utcnow()
    task.events = [*task.events, {'seq': len(task.events) + 1, 'type': 'cancelled',
                    'time': task.updated_at.isoformat(), 'payload': {'message': '用户已取消任务'}}]
    await session.commit()
    return {'status': 'cancelled'}


@router.get('/{task_id}/events')
async def task_events(task_id: str, request: Request, after: int = Query(0, ge=0), user=Depends(current_user), session=Depends(get_session)):
    """按事件序号发送已保存的进度。前端通过 fetch 请求头传递令牌，不把令牌放进 URL。"""
    await owned_task(session, task_id, user.id)
    return StreamingResponse(stream_events(task_id, user.id, request, after), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


async def stream_events(task_id: str, user_id: str, request: Request, after: int):
    """定期读取数据库中的新事件并发送给浏览器。浏览器断开或任务暂停、结束时，关闭这次连接。"""
    cursor = after
    # SSE 发送的是已保存的分析步骤，不是模型逐字生成的答案。断线只停止订阅，不会取消后台任务。
    while not await request.is_disconnected():
        async with Session() as session:
            task = await owned_task(session, task_id, user_id)
            events, status = task.events, task.status
        for event in events:
            if event['seq'] > cursor:
                # seq 标明事件的顺序。只发送浏览器尚未读过的事件，断线后也能补读这段时间的进度。
                yield 'id: ' + str(event['seq']) + '\ndata: ' + json.dumps(event, ensure_ascii=False) + '\n\n'
                cursor = event['seq']
        if status in ('succeeded', 'failed', 'cancelled', 'waiting_input'):
            yield 'event: status\ndata: ' + json.dumps({'status': status}) + '\n\n'
            break
        yield ': heartbeat\n\n'
        await asyncio.sleep(0.7)
