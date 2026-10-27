"""导入前检查字段类型、重复主键和表间关联。发现错误时明确报错，不自动丢弃数据行。"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from backend.ingestion.mapping import FIELDS, REQUIRED


def convert_value(field: str, value: str):
    """按目标字段转换输入值。金额必须非负、有限，并且小数精度不超过两位。"""
    value = str(value).strip()
    if not value:
        return None
    if field == 'order_item_id':
        number = int(value)
        if number <= 0:
            raise ValueError('明细序号必须为正整数')
        return number
    if field in ('price', 'freight_value'):
        # 金额从原始文本直接转为 Decimal，避免先转 float 产生误差。NaN、无穷大和精度不符的值都会被拒绝。
        amount = Decimal(value)
        if not amount.is_finite() or amount < 0 or amount > Decimal('9999999999999999.99') or amount != amount.quantize(Decimal('0.01')):
            raise ValueError('金额必须是非负、有限的两位小数')
        return amount
    if field.endswith('_time'):
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is not None:
            raise ValueError('首版时间模板要求无时区的原始本地时间')
        return parsed
    if len(value) > (120 if field == 'category_name' else 100):
        raise ValueError('字段长度超出模板限制')
    return value


def normalize_table(frame, table: str, mapping: dict[str, str], dataset_id: str) -> list[dict]:
    """按字段映射转换每行数据，并检查必填值和重复主键。报错时指出表名、行号和字段名。"""
    for field in REQUIRED[table]:
        if mapping.get(field) not in frame.columns:
            raise ValueError(f'{table} 缺少必填字段映射：{field}')
    if set(mapping) - set(FIELDS[table]) or any(source not in frame.columns for source in mapping.values()):
        raise ValueError(f'{table} 字段映射包含未知字段或不存在的源列')
    rows, keys = [], set()
    # 第 1 行是表头，因此数据行号从 2 开始，方便用户直接在 CSV 或 Excel 中定位错误。
    for index, source in enumerate(frame.to_dict('records'), 2):
        # dataset_id 由服务端填写，不使用文件中的同名列，防止上传数据被写入其他数据集。
        record = {'dataset_id': dataset_id}
        for field in FIELDS[table]:
            try:
                record[field] = convert_value(field, source.get(mapping.get(field), ''))
                if field in REQUIRED[table] and record[field] is None:
                    raise ValueError('必填值为空')
            except (ValueError, InvalidOperation, OverflowError) as error:
                raise ValueError(f'{table} 第 {index} 行 {field}：{error}') from None
        identifier = table.removesuffix('s') + '_id'
        # 一个订单可以有多条明细，因此明细按订单 ID 和明细序号共同判断重复，不能只看订单 ID。
        key = ((record['order_id'], record['order_item_id']) if table == 'order_items' else record[identifier])
        if key in keys:
            raise ValueError(f'{table} 第 {index} 行主键重复')
        keys.add(key)
        rows.append(record)
    if not rows:
        raise ValueError(f'{table} 没有数据行')
    return rows


def validate_relations(tables: dict[str, list[dict]]) -> None:
    """检查订单引用的客户，以及明细引用的订单和商品，是否存在于本次导入的数据中。"""
    customers = {row['customer_id'] for row in tables['customers']}
    products = {row['product_id'] for row in tables['products']}
    orders = {row['order_id'] for row in tables['orders']}
    for row in tables['orders']:
        if row['customer_id'] not in customers:
            raise ValueError(f"订单 {row['order_id']} 的客户不存在")
    for row in tables['order_items']:
        if row['order_id'] not in orders or row['product_id'] not in products:
            raise ValueError(f"商品项 {row['order_id']}/{row['order_item_id']} 的关联不存在")
