"""保存任务状态、事件和结果信息。LangGraph 的执行进度由 checkpoint 表另外保存。"""
from sqlalchemy import String, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from backend.models.base import Base, new_id, utcnow


class AnalysisTask(Base):
    """定义分析任务表，保存问题、执行状态、事件、结果引用和待恢复的答案。"""
    __tablename__ = 'analysis_tasks'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey('datasets.id', ondelete='CASCADE'), index=True)
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default='queued', index=True)
    # 每个分析任务有独立的 thread_id。config_snapshot 中的 conversation_id 则把多次分析连成一个会话。
    thread_id: Mapped[str] = mapped_column(String(36), default=new_id, unique=True)
    model_mode: Mapped[str] = mapped_column(String(10), default='demo')
    config_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    # JSONB 列保存消息和文件信息。events 按序号读取，artifacts 保存文件引用，不保存文件内容。
    # default=list 和 default=dict 在创建记录时生成新对象，避免不同记录共享同一个可变列表或字典。
    events: Mapped[list] = mapped_column(JSONB, default=list)
    artifacts: Mapped[list] = mapped_column(JSONB, default=list)
    usage: Mapped[dict] = mapped_column(JSONB, default=dict)
    # 用户补充的答案先写数据库再发任务消息。图完成或再次暂停后才清空，避免重试时丢失答案。
    resume_payload: Mapped[str | None] = mapped_column(Text)
    clarification: Mapped[str | None] = mapped_column(Text)
    error_info: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
