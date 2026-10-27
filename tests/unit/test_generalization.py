"""检查合成数据能够复现，且包含多商品和多状态，不把单一示例的通过率当作全面准确率。"""
from collections import Counter
from decimal import Decimal
from evaluations.generalization import varied_tables
from evaluations.oracle import reference_answers


def test_varied_fixtures_and_independent_truth():
    """检查同种子生成相同数据、不同种子得到不同答案，并独立核对明细求和和订单去重关系。"""
    tables = varied_tables(17, 137)
    assert tables == varied_tables(17, 137)
    assert len(tables['orders']) == 137
    assert {order['status'] for order in tables['orders']} == {'delivered', 'cancelled', 'unavailable'}
    assert max(Counter(item['order_id'] for item in tables['order_items']).values()) == 4
    reference = reference_answers(tables)
    assert reference['summary'] != reference_answers(varied_tables(83, 251))['summary']
    assert sum(item['item_amount'] for item in reference['monthly']) == reference['summary'][0]['item_amount']
    assert sum(item['order_count'] for item in reference['category']) > reference['summary'][0]['order_count']
    assert reference['summary'][0]['item_amount'] != Decimal('6550')
