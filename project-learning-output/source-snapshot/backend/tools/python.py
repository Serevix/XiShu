"""提供 Python 计算工具，只能读取当前任务已经生成的表格。"""
from backend.execution.sandbox import execute_python


async def analyze_table(task_id: str, artifact_id: str, code: str) -> dict:
    """在隔离容器中对指定表格运行分析代码，并返回新生成的表格信息。"""
    return await execute_python(task_id, artifact_id, code)
