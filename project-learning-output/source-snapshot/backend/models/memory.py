"""在 PostgreSQL 保存长期记忆记录，在本项目专用 Milvus 数据库保存对应向量。"""
from sqlalchemy import String, Text, Integer, Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from backend.models.base import Base, new_id, utcnow


class UserMemory(Base):
    """定义明确偏好记录，包括内容版本和同步状态。删除标记使旧向量立即停止使用。"""
    __tablename__ = 'user_memories'
    __table_args__ = (UniqueConstraint('user_id', 'key', name='uq_user_memory_key'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    key: Mapped[str] = mapped_column(String(40))
    value: Mapped[dict] = mapped_column(JSONB)
    text: Mapped[str] = mapped_column(Text)
    # 是否使用记忆以 PostgreSQL 为准。记录被删除或版本改变后，旧 Milvus 向量就不能再使用。
    version: Mapped[int] = mapped_column(Integer, default=1)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    # pending 和 failed 表示还需要后台同步；indexed 和 deleted 表示对应版本的向量操作已经完成。
    sync_status: Mapped[str] = mapped_column(String(16), default='pending', index=True)
    sync_error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class QuestionPattern(Base):
    """定义常问模板记录，统计不同成功任务的使用次数，不保存历史计算答案。"""
    __tablename__ = 'user_question_patterns'
    __table_args__ = (UniqueConstraint('user_id', 'fingerprint', name='uq_user_pattern_fingerprint'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    template: Mapped[str] = mapped_column(Text)
    # 只记录用户在不同成功任务中问过多少次这类问题，不保存旧金额作为下次分析的答案。
    use_count: Mapped[int] = mapped_column(Integer, default=1)
    version: Mapped[int] = mapped_column(Integer, default=1)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    sync_status: Mapped[str] = mapped_column(String(16), default='pending', index=True)
    sync_error: Mapped[str | None] = mapped_column(String(300))
    last_used_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)
