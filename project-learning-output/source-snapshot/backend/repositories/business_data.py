"""分批插入客户、商品、订单和明细。全部批次由调用方在同一个事务中提交。"""
from sqlalchemy import insert
from backend.models import Customer, Product, Order, OrderItem

# 按字典中的顺序先写客户和商品，再写订单，最后写明细，以满足表之间的外键约束。
TABLE_MODELS = {'customers': Customer, 'products': Product, 'orders': Order, 'order_items': OrderItem}


async def insert_tables(session, tables: dict[str, list[dict]]) -> None:
    """按外键依赖顺序分批写入四张表。只执行插入，不在函数内部提交事务。"""
    for table, model in TABLE_MODELS.items():
        # 分批用于控制每次 SQL 的参数数量，不表示分批提交。上层会在全部成功后统一提交。
        for offset in range(0, len(tables[table]), 500):
            await session.execute(insert(model), tables[table][offset:offset + 500])
