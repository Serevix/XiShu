"""保存商品信息，并保留缺失品类。查询时可把缺失品类归为未分类。"""
from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from backend.models.base import Base


class Product(Base):
    """定义商品表，使用数据集 ID 和商品 ID 共同确定一条记录。"""
    __tablename__ = 'products'
    dataset_id: Mapped[str] = mapped_column(ForeignKey('datasets.id', ondelete='CASCADE'), primary_key=True)
    product_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    category_name: Mapped[str | None] = mapped_column(String(120))
