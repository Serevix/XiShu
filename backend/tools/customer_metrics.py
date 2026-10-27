"""按固定的独立客户定义计算复购，模型只填写日期范围，不重新定义谁算同一位客户。"""
from datetime import date
from backend.execution.sql_executor import execute_query


def customer_repeat_requested(question: str) -> bool:
    """判断问题是否要求复购分析，让报告校验时确认使用了专门的复购计算结果。"""
    normalized = question.lower()
    return any(word in normalized for word in ('复购', 'repeat_customer', 'repeat customer', 'repurchase', '购买至少两单', '购买至少两次'))


async def customer_repeat_metrics(task_id: str, dataset_id: str, valid_status: str,
                                  start_date: str | None = None, end_date: str | None = None) -> dict:
    """按独立客户标识合并有效订单，统计至少购买两单的客户及其占比。开始日包含，结束日不包含。"""
    start = date.fromisoformat(start_date) if start_date else None
    end = date.fromisoformat(end_date) if end_date else None
    if start and end and start >= end:
        raise ValueError('复购时间范围应为开始日期早于结束日期，结束日期不包含在内')
    filters = []
    if start:
        filters.append(f"o.purchase_time >= '{start.isoformat()}'")
    if end:
        filters.append(f"o.purchase_time < '{end.isoformat()}'")
    where = ' WHERE ' + ' AND '.join(filters) if filters else ''
    # Olist 的 customer_id 用于订单关联，customer_unique_id 才能识别同一人在不同订单中的购买行为。
    # 先按独立客户统计订单数，再计算至少两单客户的比例。缺少独立标识的订单单独统计，不猜测其客户归属。
    query = '''WITH valid_customer_orders AS (
        SELECT c.customer_unique_id, o.order_id
        FROM orders o JOIN customers c ON c.dataset_id=o.dataset_id AND c.customer_id=o.customer_id''' + where + '''
    ), customer_counts AS (
        SELECT customer_unique_id, COUNT(DISTINCT order_id) AS n
        FROM valid_customer_orders WHERE customer_unique_id IS NOT NULL GROUP BY customer_unique_id
    ) SELECT COUNT(*) AS unique_customers,
        COUNT(CASE WHEN n>=2 THEN 1 END) AS repeat_customers,
        ROUND(100.0 * COUNT(CASE WHEN n>=2 THEN 1 END) / NULLIF(COUNT(*),0),2) AS repeat_rate,
        (SELECT COUNT(DISTINCT order_id) FROM valid_customer_orders WHERE customer_unique_id IS NULL) AS unidentified_orders
        FROM customer_counts'''
    # 固定指标查询也经过统一执行器，仍须遵守只读权限、数据集范围和有效订单状态限制。
    result = await execute_query(task_id, dataset_id, query, valid_status)
    return {**result, 'metric_id': 'customer_repeat', 'customer_identity': 'customer_unique_id',
            'date_range': {'start_inclusive': start_date, 'end_exclusive': end_date}}
