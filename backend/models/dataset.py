"""保存数据集信息。数据集导入就绪后，不在原数据集上继续修改业务内容。"""
from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from backend.models.base import Base, new_id, utcnow


class Dataset(Base):
    """定义数据集表，记录上传文件、字段映射、导入状态和指标口径。"""
    __tablename__ = 'datasets'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    name: Mapped[str] = mapped_column(String(120))
    source_type: Mapped[str] = mapped_column(String(20), default='upload')
    status: Mapped[str] = mapped_column(String(20), default='uploaded', index=True)
    file_manifest: Mapped[dict] = mapped_column(JSONB, default=dict)
    field_mapping: Mapped[dict] = mapped_column(JSONB, default=dict)
    metric_definitions: Mapped[dict] = mapped_column(JSONB, default=dict)
    import_summary: Mapped[dict] = mapped_column(JSONB, default=dict)
    error_info: Mapped[str | None] = mapped_column(String(2000))
    data_version: Mapped[str] = mapped_column(String(36), default=new_id)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)
