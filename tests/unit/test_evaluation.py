"""检查评分器不会把错误金额、漏行或去重错误判为正确。"""
from decimal import Decimal
from evaluations.oracle import reference_answers, compare_rows
from scripts.generate_demo_data import generate_tables
from evaluations.benchmark import candidates_correct, matching_grain, normalize_columns, compare_candidate


def test_independent_reference_and_overlap():
    """独立计算汇总金额，并检查跨品类订单重复出现时的统计，以及复购所用的客户标识。"""
    answer = reference_answers(generate_tables())
    assert answer['summary'] == [{'order_count': 66, 'item_amount': Decimal('6550'), 'freight_amount': Decimal('684')}]
    assert sum(row['order_count'] for row in answer['category']) == 74
    assert answer['customer'][0]['unique_customers'] == 33
    assert answer['customer'][0]['repeat_customers'] == 33


def test_scoring_rejects_wrong_or_missing_rows():
    """检查错误数字、重复维度、缺行和非整数计数都会被拒绝，不被舍入容差放过。"""
    expected = [{'category': 'books', 'amount': Decimal('100'), 'n': 2}, {'category': 'home', 'amount': Decimal('200'), 'n': 3}]
    assert compare_rows([{'category': 'home', 'amount': '200.00', 'n': 3}, {'category': 'books', 'amount': 100, 'n': 2}], expected)['passed']
    assert not compare_rows([{'category': 'books', 'amount': 100, 'n': 2}], expected)['passed']
    assert not compare_rows([{'category': 'home', 'amount': 201, 'n': 3}, {'category': 'books', 'amount': 100, 'n': 2}], expected)['passed']
    assert not compare_rows([{'category': 'home', 'amount': 200, 'n': 3}, {'category': 'books', 'amount': 100, 'n': 2.001}], expected)['passed']


def test_conflicting_saved_tables_cannot_pass():
    """检查报告同时保留错误旧表和正确新表时仍判失败，不只检查最后一张表。"""
    expected = [{'amount': Decimal('100')}]
    wrong = compare_rows([{'amount': '999'}], expected)
    correct = compare_rows([{'amount': '100'}], expected)
    assert not candidates_correct([wrong, correct])
    assert candidates_correct([correct, correct])
    assert not candidates_correct([])


def test_extra_breakdown_has_different_grain():
    """检查整体汇总与合法月度拆分分别评分，避免因为共享列名而误判。"""
    expected = [{'order_count': 66, 'item_amount': Decimal('6550')}]
    assert matching_grain(['order_count', 'item_amount', 'aov'], {}, expected)
    assert not matching_grain(['month', 'order_count', 'item_amount'], {}, expected)
    assert not matching_grain(['category_name', 'order_count', 'item_amount'], {'category_name':'category'}, expected)


def test_canonical_amount_is_not_overwritten_by_alias():
    """商品金额与含运费金额同时存在时保持各自原值，不因历史列别名造成假错误。"""
    assert normalize_columns({'item_amount':'100','total_amount':'130'}, {'total_amount':'item_amount'})['item_amount']=='100'


def test_subset_needs_correct_numbers_and_complete_sibling():
    """补充子集允许保留，但独自缺行不能通过，错误子集也不能借完整表掩盖。"""
    expected=[{'state':'A','amount':Decimal('100')},{'state':'B','amount':Decimal('200')}]
    complete=compare_candidate(expected,expected)
    subset=compare_candidate([expected[1]],expected)
    assert not candidates_correct([subset])
    assert candidates_correct([complete,subset])
    wrong=compare_candidate([{'state':'B','amount':'201'}],expected)
    assert not candidates_correct([complete,wrong])
