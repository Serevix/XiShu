"""以固定业务粒度计算常用销售指标，避免模型自行定义运费和客单价。"""
from datetime import date
from backend.execution.sql_executor import execute_query


async def sales_metrics(task_id: str, dataset_id: str, valid_status: str,
                         start_date: str | None = None, end_date: str | None = None,
                         group_by: str = 'total') -> dict:
    """先逐订单汇总商品项，再统计订单量与金额；开始包含、结束不包含，无明细订单金额为零。"""
    start = date.fromisoformat(start_date) if start_date else None
    end = date.fromisoformat(end_date) if end_date else None
    if start and end and start >= end:
        raise ValueError('销售时间范围必须开始早于结束')
    if group_by not in ('total', 'month'):
        raise ValueError('销售指标分组只支持 total 或 month')
    filters = []
    if start:
        filters.append(f"o.purchase_time >= '{start.isoformat()}'")
    if end:
        filters.append(f"o.purchase_time < '{end.isoformat()}'")
    where = ' WHERE ' + ' AND '.join(filters) if filters else ''
    # 先按订单聚合，避免一个订单有多项商品时重复计算订单数；运费必须逐商品项求和。
    prefix = "TO_CHAR(purchase_time,'YYYY-MM') AS month, " if group_by == 'month' else ''
    suffix = " GROUP BY TO_CHAR(purchase_time,'YYYY-MM') ORDER BY month" if group_by == 'month' else ''
    query = '''WITH order_totals AS (
        SELECT o.order_id, o.purchase_time,
               COALESCE(SUM(i.price),0) AS amount,
               COALESCE(SUM(i.freight_value),0) AS freight
        FROM orders o LEFT JOIN order_items i
          ON i.dataset_id=o.dataset_id AND i.order_id=o.order_id''' + where + '''
        GROUP BY o.order_id,o.purchase_time
    ) SELECT ''' + prefix + '''COUNT(*) AS order_count,
        COALESCE(SUM(amount),0) AS item_amount,
        COALESCE(SUM(freight),0) AS freight_amount,
        COALESCE(SUM(amount+freight),0) AS total_amount,
        ROUND(SUM(amount)/NULLIF(COUNT(*),0),2) AS aov
        FROM order_totals''' + suffix
    if group_by == 'month':
        # 保存常见月度差值和百分比，避免报告口算或额外请求补算；统一保留两位小数。
        query = '''WITH monthly AS (''' + query + ''') SELECT *,
            item_amount-LAG(item_amount) OVER (ORDER BY month) AS amount_diff,
            order_count-LAG(order_count) OVER (ORDER BY month) AS order_diff,
            ROUND(100.0*(item_amount-LAG(item_amount) OVER (ORDER BY month))/NULLIF(LAG(item_amount) OVER (ORDER BY month),0),2) AS amount_growth,
            ROUND(100.0*(order_count-LAG(order_count) OVER (ORDER BY month))/NULLIF(LAG(order_count) OVER (ORDER BY month),0),2) AS order_growth
            FROM monthly ORDER BY month'''
    result = await execute_query(task_id, dataset_id, query, valid_status)
    return {**result, 'metric_id': 'sales_summary', 'group_by': group_by,
            'date_range': {'start_inclusive': start_date, 'end_exclusive': end_date}}
