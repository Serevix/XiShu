"""创建分析任务并发送队列消息。发送失败时保留任务，由恢复任务稍后重试。"""
from fastapi import HTTPException
from backend.core.config import get_settings
from backend.models.analysis_task import AnalysisTask
from backend.repositories.datasets import owned_dataset
from sqlalchemy import select
from backend.models import Conversation
from backend.context.conversations import owned_conversation, append_message


def serialize_task(task: AnalysisTask, include_events: bool = False) -> dict:
    """整理接口需要的任务状态和结果信息，不返回图线程 ID 或数据库配置。"""
    body = {'id': task.id, 'dataset_id': task.dataset_id, 'question': task.question,
            'status': task.status, 'mode': task.model_mode, 'artifacts': task.artifacts,
            'usage': task.usage, 'error': task.error_info, 'clarification': task.clarification,
            'created_at': task.created_at.isoformat(), 'updated_at': task.updated_at.isoformat()}
    body['conversation_id'] = task.config_snapshot.get('conversation_id')
    if include_events:
        body['events'] = task.events
    return body


async def create_task(session, user_id: str, payload) -> AnalysisTask:
    """检查数据集归属和就绪状态后创建任务，并保存本任务使用的口径、数据版本和模型配置。"""
    dataset = await owned_dataset(session, str(payload.dataset_id), user_id)
    if dataset.status != 'ready':
        raise HTTPException(409, '数据集尚未导入成功')
    settings = get_settings()
    if '[时间范围]' in payload.question:
        raise HTTPException(422, '请先把常用问题模板中的 [时间范围] 填写完整')
    if payload.conversation_id:
        conversation = await owned_conversation(session, str(payload.conversation_id), user_id)
        # 先锁住会话记录再检查是否存在未结束任务，让同时提交的请求依次检查，避免创建重复的活动任务。
        await session.refresh(conversation, with_for_update=True)
        if conversation.dataset_id != dataset.id:
            raise HTTPException(409, '会话绑定原数据集，切换数据集时请新建会话')
        active = await session.scalar(select(AnalysisTask.id).where(AnalysisTask.user_id == user_id,
                      AnalysisTask.config_snapshot['conversation_id'].astext == conversation.id,
                      AnalysisTask.status.in_(['queued','running','waiting_input'])).limit(1))
        if active:
            raise HTTPException(409, '当前会话有未结束任务，请等待、补充或取消后再提问')
    else:
        conversation = Conversation(user_id=user_id, dataset_id=dataset.id)
        session.add(conversation)
        # flush 执行插入并取得默认生成的 ID，但还没有提交事务，后面出错时仍可一起回滚。
        await session.flush()
    mode = payload.mode or settings.model_mode
    if mode == 'llm' and not settings.model_api_key:
        raise HTTPException(422, '请先在 .env 配置 MODEL_API_KEY 并重启 API 和 Worker')
    task = AnalysisTask(user_id=user_id, dataset_id=dataset.id, question=payload.question.strip(),
                        model_mode=mode, config_snapshot={'metrics': dataset.metric_definitions,
                        'data_version': dataset.data_version, 'model': settings.model_name if mode == 'llm' else 'rule-demo',
                        'max_steps': settings.max_agent_steps})
    task.config_snapshot = {**task.config_snapshot, 'conversation_id': conversation.id}
    # 保存当前口径、数据版本和模型信息，后续按本任务记录执行，便于查看当时使用的分析条件。
    session.add(task)
    await session.flush()
    # await 等待消息追加完成，这里不接收返回值。用户问题与任务记录最后一起提交。
    await append_message(session, conversation, task.id, 'user', task.question, task.question)
    await session.commit()
    return task


def dispatch(job: str, identifier: str) -> bool:
    """向 Celery 发送任务消息。发送成功返回 True，失败返回 False，数据库中已保存的任务不被删除。"""
    from backend.workers.celery_app import celery_app
    try:
        # 任务记录已经提交后才发送任务 ID。Worker 再根据 ID 查库，消息中不包含业务数据或连接凭据。
        celery_app.send_task(job, args=[identifier], retry=False)
        return True
    except Exception:
        # PostgreSQL 和 Redis 没有共同事务，所以发送失败后保留 queued 任务，让 Beat 稍后再次发送。
        return False
