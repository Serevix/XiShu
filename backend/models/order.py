"""保存订单，并用包含数据集 ID 的外键防止关联到其他数据集的客户。"""
from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, ForeignKeyConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from backend.models.base import Base


class Order(Base):
    """定义订单表，保存购买和配送时间。业务时间按原始本地时间保存，不自动补上时区。"""
    __tablename__ = 'orders'
    __table_args__ = (
        ForeignKeyConstraint(['dataset_id', 'customer_id'], ['customers.dataset_id', 'customers.customer_id']),
        Index('ix_orders_dataset_time', 'dataset_id', 'purchase_time'),
    )
    dataset_id: Mapped[str] = mapped_column(ForeignKey('datasets.id', ondelete='CASCADE'), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30))
    purchase_time: Mapped[datetime] = mapped_column(DateTime)
    delivered_time: Mapped[datetime | None] = mapped_column(DateTime)
    estimated_delivery_time: Mapped[datetime | None] = mapped_column(DateTime)
