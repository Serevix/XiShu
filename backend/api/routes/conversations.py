"""读取会话原文和摘要进度，并检查会话是否属于当前用户。"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from backend.api.dependencies import current_user
from backend.core.database import get_session
from backend.models import Conversation, ConversationMessage
from backend.context.conversations import owned_conversation

router = APIRouter(prefix='/api/conversations', tags=['会话上下文'])


@router.get('')
async def list_conversations(user=Depends(current_user), session=Depends(get_session)):
    """列出当前用户最近的会话及其数据集信息，不返回其他用户的会话。"""
    rows = await session.scalars(select(Conversation).where(Conversation.user_id == user.id).order_by(Conversation.updated_at.desc()).limit(50))
    return [{'id':row.id,'dataset_id':row.dataset_id,'created_at':row.created_at.isoformat(), 'updated_at':row.updated_at.isoformat()} for row in rows]


@router.get('/{identifier}')
async def get_conversation(identifier: str, after: int = Query(0,ge=0), limit: int = Query(20,ge=1,le=100),
                           user=Depends(current_user), session=Depends(get_session)):
    """按消息序号分页返回原文，同时返回摘要。生成摘要不会替换或删除原始消息。"""
    conversation = await owned_conversation(session, identifier, user.id)
    messages = await session.scalars(select(ConversationMessage).where(ConversationMessage.conversation_id == identifier,
                                      ConversationMessage.sequence > after).order_by(ConversationMessage.sequence).limit(limit))
    return {'id':conversation.id,'dataset_id':conversation.dataset_id,'summary':conversation.summary,
            'summary_cursor':conversation.summary_cursor,'summary_error':conversation.summary_error, 'summary_usage':conversation.summary_usage,
            'last_sequence':conversation.next_sequence - 1,
            'messages':[{'sequence':row.sequence,'role':row.role,'content':row.content,'task_id':row.task_id} for row in messages]}
