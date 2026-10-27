"""使用独立的只读账号执行查询，并根据已授权任务设置数据库行级权限所需的数据集范围。"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from backend.core.config import get_settings
from backend.core.storage import write_table
from backend.execution.policy import validate_sql
from backend.execution.metric_scope import scoped_query


async def execute_query(task_id: str, dataset_id: str, query: str, valid_status: str | None = None) -> dict:
    """在只读事务中指定数据集和查询超时，执行查询并检查返回行数，成功后保存表格结果。"""
    settings = get_settings()
    # 先检查 SQL 语法树，再添加订单状态限制，最后通过只读账号和行级权限查询允许的数据。
    normalized = validate_sql(query)
    effective = scoped_query(normalized, valid_status) if valid_status is not None else normalized
    parameters = {'analysis_valid_status': valid_status} if valid_status is not None else {}
    reader_engine = create_async_engine(settings.sql_reader_url, poolclass=NullPool)
    try:
        async with reader_engine.begin() as connection:
            await connection.execute(text('SET TRANSACTION READ ONLY'))
            # 数据库行级权限从 app.dataset_id 读取本次数据集。true 让这个配置只在当前事务中生效。
            await connection.execute(text("SELECT set_config('app.dataset_id', :id, true)"), {'id': dataset_id})
            await connection.execute(text("SELECT set_config('statement_timeout', :timeout, true)"), {'timeout': str(settings.sql_timeout_ms)})
            # 内部 SQL 完成统计，外层 LIMIT 只限制输出数量。结果超过上限时会报错，不会直接交付截断后的表格。
            cursor = await connection.execute(text(f'SELECT * FROM ({effective}) AS bounded_result LIMIT {settings.max_sql_rows + 1}'), parameters)
            columns = list(cursor.keys())
            if len(columns) > 40 or any(len(column) > 80 for column in columns):
                raise ValueError('结果列数量或列名长度超出限制，请减少字段或使用简短别名')
            if len(set(columns)) != len(columns):
                raise ValueError('结果列名重复，请为查询列指定唯一别名')
            rows = [dict(row) for row in cursor.mappings()]
            # 比上限多读一行，用来判断是否超出限制。超出就报错，避免把缺行的表格当作完整结果交付。
            if len(rows) > settings.max_sql_rows:
                raise ValueError('结果行数超出限制，请在 SQL 中聚合或明确限定范围')
        return write_table(task_id, 'SQL 查询结果', columns, rows, sql=normalized, effective_sql=effective,
                           dataset_id=dataset_id, metric_scope={'valid_status': valid_status, 'enforced': valid_status is not None})
    finally:
        await reader_engine.dispose()
