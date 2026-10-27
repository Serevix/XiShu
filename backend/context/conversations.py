"""保存完整会话消息，并逐步总结旧消息。一个会话中的分析任务始终使用同一数据集。"""
import asyncio
import json
from fastapi import HTTPException
from sqlalchemy import select, text
from backend.core.database import Session, engine
from backend.core.config import get_settings
from backend.core.storage import read_artifact
from backend.models import Conversation, ConversationMessage, AnalysisTask
from backend.models.base import utcnow
from backend.context.summary import summarize


async def owned_conversation(session, identifier: str, user_id: str) -> Conversation:
    """查找当前用户的会话。会话不存在或属于其他用户时，都返回会话不存在错误。"""
    row = await session.scalar(select(Conversation).where(Conversation.id == identifier, Conversation.user_id == user_id))
    if row is None:
        raise HTTPException(404, '会话不存在')
    return row


async def append_message(session, conversation: Conversation, task_id: str, role: str, content: str, context_text: str):
    """按会话序号追加消息。调用方负责加锁和提交事务；同一任务的同一角色只保存一次消息。"""
    existing = await session.scalar(select(ConversationMessage.id).where(ConversationMessage.task_id == task_id, ConversationMessage.role == role))
    # scalar 查到消息时返回其 ID，没查到时返回 None。同一任务、同一角色已有消息时，跳过重复追加。
    if existing:
        return
    # 本函数直接修改 session 和 conversation，正常结束时返回 None。调用方使用 await 等待操作完成即可。
    # add 只是登记待插入对象。调用方统一 commit，保证新任务和消息一起保存；异常时一起回滚。
    session.add(ConversationMessage(conversation_id=conversation.id, task_id=task_id, sequence=conversation.next_sequence,
                                    role=role, content=content, context_text=context_text))
    conversation.next_sequence += 1
    conversation.updated_at = utcnow()


async def finish_conversation(task_id: str, before_completion: bool = False):
    """保存已生成的完整报告作为会话消息。可在成功状态提交前保存，也可在任务成功后补写遗漏的报告。"""
    async with Session() as session:
        task = await session.get(AnalysisTask, task_id)
        if not task or task.status != 'succeeded' and not (before_completion and task.status == 'running') or not task.config_snapshot.get('conversation_id'):
            return
        report = next((item for item in task.artifacts if item['kind'] == 'report'), None)
        if not report:
            return
        _, path = read_artifact(report['id'], task_id)
        content = await asyncio.to_thread(path.read_text, encoding='utf-8')
        # 会话原文保留完整报告。发给模型的历史内容只保留问题、说明、字段和少量结果预览，以减少输入长度。
        overview = {'historical_only': True, 'report_completed':True, 'question': task.question, 'report_intro': content.split('## 结果')[0],
                    'charts':[{key:item[key] for key in ('id','source_id','chart_kind','source_row_count','plotted_row_count') if key in item}
                              for item in task.artifacts if item['kind']=='chart'],
                    'tables': [{key: item[key] for key in ('columns','row_count','preview') if key in item}
                               for item in task.artifacts if item['kind'] == 'table']}
        context = json.dumps(overview, ensure_ascii=False)
        if len(context) > 6000:
            context = json.dumps({'historical_only':True, 'report_completed':True, 'charts':overview['charts'], 'question':task.question, 'report_intro':overview['report_intro'][:3000],
                                  'columns':[item.get('columns', []) for item in task.artifacts if item['kind']=='table']}, ensure_ascii=False)
        identifier, user_id = task.config_snapshot['conversation_id'], task.user_id
    async with Session.begin() as session:
        # Session.begin 正常退出时自动提交，抛出异常时自动回滚，所以这里不用再次调用 commit。
        conversation = await owned_conversation(session, identifier, user_id)
        await session.refresh(conversation, with_for_update=True)
        await append_message(session, conversation, task_id, 'assistant', content, context)


async def conversation_context(identifier: str, user_id: str, task_id: str, mode: str) -> dict:
    """依次总结同一会话中较旧的消息，并保留近期消息。摘要失败时不删除原文，也不推进摘要进度。"""
    settings = get_settings()
    async with engine.connect() as lock:
        await lock.execute(text('SELECT pg_advisory_lock(hashtextextended(:key,0))'), {'key':'conversation:' + identifier})
        await lock.commit()
        try:
            async with Session() as session:
                conversation = await owned_conversation(session, identifier, user_id)
                rows = list(await session.scalars(select(ConversationMessage).where(ConversationMessage.conversation_id == identifier,
                                    ConversationMessage.task_id != task_id).order_by(ConversationMessage.sequence)))
                summary, cursor = conversation.summary, conversation.summary_cursor
            recent = rows[-settings.context_recent_messages:]
            # cursor 记录已成功总结到哪条消息。近期消息保留原文，较旧的消息只处理尚未总结的部分。
            older = [row for row in rows[:-settings.context_recent_messages] if row.sequence > cursor]
            if older and mode == 'llm':
                try:
                    summary, usage = await summarize(summary, [{'role':row.role,'content':row.context_text} for row in older])
                    cursor = older[-1].sequence
                    # 摘要生成成功后，再一起保存摘要和进度。如果先更新进度，模型调用失败后就可能跳过尚未总结的消息。
                    async with Session.begin() as session:
                        current = await session.get(Conversation, identifier, with_for_update=True)
                        current.summary, current.summary_cursor, current.summary_error = summary, cursor, None
                        current.summary_usage = {key:current.summary_usage.get(key,0) + usage.get(key,0) for key in usage}
                except Exception as error:
                    async with Session.begin() as session:
                        current = await session.get(Conversation, identifier, with_for_update=True)
                        current.summary_error = '会话摘要暂不可用：' + type(error).__name__
                    # 摘要失败时保持原进度，下次任务仍会尝试总结这些消息。
            messages = [{'role':row.role, 'content':row.context_text} for row in recent]
            return {'summary':summary, 'summary_cursor':cursor, 'messages':messages, 'history_count':len(rows)}
        finally:
            await lock.execute(text('SELECT pg_advisory_unlock(hashtextextended(:key,0))'), {'key':'conversation:' + identifier})
            await lock.commit()
