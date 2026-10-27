"""保存订单明细，每行代表一个商品项。金额使用 NUMERIC 类型保留小数精度。"""
from decimal import Decimal
from sqlalchemy import String, Integer, Numeric, ForeignKey, ForeignKeyConstraint, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from backend.models.base import Base


class OrderItem(Base):
    """定义明细表，保存商品金额和运费，并通过约束拒绝负金额及跨数据集关联。"""
    __tablename__ = 'order_items'
    __table_args__ = (
        ForeignKeyConstraint(['dataset_id', 'order_id'], ['orders.dataset_id', 'orders.order_id']),
        ForeignKeyConstraint(['dataset_id', 'product_id'], ['products.dataset_id', 'products.product_id']),
        CheckConstraint('price >= 0 AND freight_value >= 0', name='ck_item_nonnegative'),
        CheckConstraint('order_item_id > 0', name='ck_item_positive_id'),
    )
    dataset_id: Mapped[str] = mapped_column(ForeignKey('datasets.id', ondelete='CASCADE'), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    order_item_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[str] = mapped_column(String(100))
    price: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    freight_value: Mapped[Decimal] = mapped_column(Numeric(18, 2))
