"""向模型说明四表字段、表间关系和已经确认的统计口径。"""
from backend.ingestion.mapping import FIELDS


def describe_schema(metrics: dict) -> dict:
    """返回数据字典，解释连接字段和金额所对应的商品项，提醒模型避免联表后重复统计。"""
    return {'dialect': 'postgresql', 'tables': {name: ['dataset_id', *fields] for name, fields in FIELDS.items()}, 'metrics': metrics,
            'supported_statistics': ['COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'ROUND', 'ABS', 'COALESCE', 'NULLIF', 'CAST',
                                     'DATE_TRUNC', 'TO_CHAR', 'EXTRACT', 'LAG', 'LEAD'],
            'relations': ['orders.customer_id = customers.customer_id', 'order_items.order_id = orders.order_id',
                          'order_items.product_id = products.product_id'],
            'field_meanings': {'customers.customer_id': '订单关联客户记录，同一自然客户的不同订单可能使用不同记录 ID',
                               'customers.customer_unique_id': '跨订单识别独立客户；复购必须先按此字段合并订单',
                               'order_items.price': '单个商品项金额，NUMERIC，不含运费',
                               'order_items.freight_value': '单个商品项的运费，NUMERIC；总运费逐行 SUM；按订单预聚合仍用 SUM，不能取 MAX 或去重',
                               'orders.purchase_time': '无时区购买时间；时间范围左闭右开'},
            'notes': ['所有联合关联包含 dataset_id；查询已由 RLS 限定当前数据集',
                      '分析中的 / 使用 NUMERIC 定点数除法；计数比例不会执行整数截断，零分母仍需 NULLIF',
                      '订单及明细由执行器强制限定 metrics.valid_status；不要写冲突状态',
                      '复购使用 customer_repeat_metrics；不得先按 customer_id 统计再直接判断复购',
                      '订单量对 order_id 去重；一行 order_items 表示一个商品项',
                      'price 为定点金额，不含 freight_value；没有退款或成本数据']}
