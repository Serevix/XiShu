"""提供数据库模型基类，以及通用的 UUID 和 UTC 时间函数。"""

from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """定义所有数据库模型共用的基类和表结构信息。"""


def new_id() -> str:
    """生成 UUID 作为资源 ID。访问资源时仍要检查用户归属，不能只依赖 ID 难以猜测。"""
    return str(uuid4())


def utcnow() -> datetime:
    """返回带 UTC 时区的当前时间，用于记录任务和导入的创建、更新时间。"""
    return datetime.now(timezone.utc)
