"""保存客户记录，分别记录订单使用的客户 ID 和合并同一人多次购买的独立客户标识。"""
from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from backend.models.base import Base


class Customer(Base):
    """定义客户表，使用数据集 ID 和客户 ID 共同确定一条记录。"""
    __tablename__ = 'customers'
    dataset_id: Mapped[str] = mapped_column(ForeignKey('datasets.id', ondelete='CASCADE'), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    customer_unique_id: Mapped[str | None] = mapped_column(String(100))
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(50))
