"""定义本项目独立的用户表，不访问项目一的用户数据。"""
from sqlalchemy import String, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from backend.models.base import Base, new_id, utcnow


class User(Base):
    """定义用户表，保存不重复的用户名和密码哈希。"""
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(40), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)
