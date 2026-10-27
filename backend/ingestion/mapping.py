"""定义项目字段与 Olist 原始列名的对应关系。用户可以在预览页面修改建议映射。"""

FIELDS = {
    'customers': ['customer_id', 'customer_unique_id', 'city', 'state'],
    'products': ['product_id', 'category_name'],
    'orders': ['order_id', 'customer_id', 'status', 'purchase_time', 'delivered_time', 'estimated_delivery_time'],
    'order_items': ['order_id', 'order_item_id', 'product_id', 'price', 'freight_value'],
}
REQUIRED = {
    'customers': ['customer_id'], 'products': ['product_id'],
    'orders': ['order_id', 'customer_id', 'status', 'purchase_time'],
    'order_items': FIELDS['order_items'],
}
ALIASES = {
    'city': 'customer_city', 'state': 'customer_state', 'category_name': 'product_category_name',
    'status': 'order_status', 'purchase_time': 'order_purchase_timestamp',
    'delivered_time': 'order_delivered_customer_date', 'estimated_delivery_time': 'order_estimated_delivery_date',
}
DEFAULT_METRICS = {'currency': 'BRL', 'valid_status': 'delivered', 'time_field': 'purchase_time',
                   'amount': 'SUM(order_items.price)，不含运费', 'orders': 'COUNT(DISTINCT orders.order_id)',
                   'zero_division': '返回空值并说明分母为零', 'timezone': '原始本地时间，不擅自添加时区'}


def infer_table(name: str) -> str | None:
    """根据项目标准文件名或 Olist 官方文件名，识别客户、商品、订单和明细表。"""
    normalized = name.lower().removesuffix('.csv').removesuffix('.xlsx')
    for table in FIELDS:
        if normalized in (table, f'olist_{table}_dataset'):
            return table
    return None


def default_mapping(columns: list[str], table: str) -> dict[str, str]:
    """为实际存在的同名列或 Olist 别名生成字段映射，不猜测其他列的业务含义。"""
    return {field: field if field in columns else ALIASES[field]
            for field in FIELDS[table] if field in columns or ALIASES.get(field) in columns}
