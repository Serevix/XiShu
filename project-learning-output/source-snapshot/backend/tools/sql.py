"""提供 SQL 查询工具。数据集 ID 由任务指定，不接受模型自行提供。"""
from backend.execution.sql_executor import execute_query


async def search_data(task_id: str, dataset_id: str, query: str, valid_status: str) -> dict:
    """调用受限查询执行器，运行 SQL 并返回保存好的表格结果。"""
    return await execute_query(task_id, dataset_id, query, valid_status)
