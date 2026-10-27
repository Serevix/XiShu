"""根据原始四表独立计算答案，不复用 Agent 的 SQL 或报告检查逻辑。"""
from collections import defaultdict
from decimal import Decimal


def reference_answers(tables: dict, valid_status: str = 'delivered') -> dict:
    """用字典关联四表并通过 Decimal 汇总，生成订单、月份、品类、地区和客户指标的基准答案。"""
    orders = {row['order_id']: row for row in tables['orders'] if row['status'] == valid_status}
    customers = {row['customer_id']: row for row in tables['customers']}
    products = {row['product_id']: row for row in tables['products']}
    amounts, freight = Decimal(0), Decimal(0)
    month, category, region = {}, {}, {}
    for item in tables['order_items']:
        order = orders.get(item['order_id'])
        if order is None:
            continue
        price = Decimal(str(item['price']))
        amounts += price
        freight += Decimal(str(item['freight_value']))
        keys = (order['purchase_time'][:7], products[item['product_id']]['category_name'], customers[order['customer_id']]['state'])
        for target, key in zip((month, category, region), keys):
            entry = target.setdefault(key, {'amount': Decimal(0), 'orders': set()})
            entry['amount'] += price
            entry['orders'].add(order['order_id'])
    valid_customers = defaultdict(set)
    for order in orders.values():
        valid_customers[customers[order['customer_id']]['customer_unique_id']].add(order['order_id'])
    repeated = sum(len(ids) > 1 for ids in valid_customers.values())
    summary = [{'order_count': len(orders), 'item_amount': amounts, 'freight_amount': freight}]
    categories = [{'category': key, 'item_amount': value['amount'], 'order_count': len(value['orders']),
                   'share_percent': value['amount'] / amounts * 100 if amounts else None} for key, value in category.items()]
    monthly = [{'month': key, 'item_amount': value['amount'], 'order_count': len(value['orders'])} for key, value in month.items()]
    regions = [{'state': key, 'item_amount': value['amount'], 'order_count': len(value['orders'])} for key, value in region.items()]
    before, after = month.get('2017-10', {}).get('amount', Decimal(0)), month.get('2017-11', {}).get('amount', Decimal(0))
    return {'summary': summary, 'monthly': monthly, 'category': categories, 'region': regions,
            'change': [{'previous_amount': before, 'current_amount': after, 'delta_amount': after - before,
                        'change_percent': (after - before) / before * 100 if before else None}],
            'aov': [{'order_count': len(orders), 'item_amount': amounts, 'freight_amount': freight,
                     'total_amount': amounts + freight, 'aov': amounts / len(orders) if orders else None}],
            'customer': [{'unique_customers': len(valid_customers), 'repeat_customers': repeated,
                          'repeat_rate': repeated / len(valid_customers) * 100 if valid_customers else None}],
            'empty': [{'order_count': 0, 'item_amount': Decimal(0)}]}


def compare_rows(actual: list[dict], expected: list[dict], tolerance: Decimal = Decimal('0.011')) -> dict:
    """先按维度匹配全部行，再检查数值是否位于允许的绝对误差范围内。"""
    errors = []
    if len(actual) != len(expected):
        errors.append(f'行数不一致：实际 {len(actual)}，标准 {len(expected)}')
    remaining = list(actual)
    for target in expected:
        dimensions = {key: value for key, value in target.items() if isinstance(value, str)}
        match = next((row for row in remaining if all(str(row.get(key)) == value for key, value in dimensions.items())), None)
        if match is None:
            errors.append('缺少维度：' + str(dimensions))
            continue
        remaining.remove(match)
        for key, value in target.items():
            observed = match.get(key)
            if value is None:
                correct = observed is None
            elif isinstance(value, str):
                correct = str(observed) == value
            else:
                try:
                    difference = abs(Decimal(str(observed)) - Decimal(str(value)))
                    # 计数必须为整数且完全相等，金额和百分比则允许规定范围内的两位小数舍入误差。
                    correct = difference == 0 if isinstance(value, int) else difference <= tolerance
                except Exception:
                    correct = False
            if not correct:
                errors.append(f'{dimensions} {key}：实际 {observed}，标准 {value}')
    return {'passed': not errors, 'errors': errors}
