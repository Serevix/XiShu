"""保存跨分析任务的会话原文和摘要进度，不用于替代单次任务的 checkpoint。"""
from sqlalchemy import String, Text, Integer, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from backend.models.base import Base, new_id, utcnow


class Conversation(Base):
    """定义会话记录，绑定用户和数据集。只有摘要成功生成，才更新已总结到的消息序号。"""
    __tablename__ = 'analysis_conversations'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey('datasets.id', ondelete='CASCADE'), index=True)
    summary: Mapped[str] = mapped_column(Text, default='')
    # summary_cursor 记录已经总结到哪条消息；next_sequence 为下一条原文分配序号，两者用途不同。
    summary_cursor: Mapped[int] = mapped_column(Integer, default=0)
    next_sequence: Mapped[int] = mapped_column(Integer, default=1)
    summary_error: Mapped[str | None] = mapped_column(String(300))
    summary_usage: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ConversationMessage(Base):
    """保存原始问题和完整报告，同时用 context_text 保存供模型读取的精简内容。"""
    __tablename__ = 'conversation_messages'
    # 第一项唯一约束防止同一任务角色被重复写入，第二项防止同一会话出现重复消息序号。
    __table_args__ = (UniqueConstraint('task_id', 'role', name='uq_conversation_task_role'),
                      UniqueConstraint('conversation_id', 'sequence', name='uq_conversation_sequence'))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('analysis_conversations.id', ondelete='CASCADE'), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey('analysis_tasks.id', ondelete='CASCADE'))
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    # content 保存完整问题或报告，context_text 保存供模型总结的内容，可以省去报告中的大表格。
    content: Mapped[str] = mapped_column(Text)
    context_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)
