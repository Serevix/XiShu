"""检查状态限制、数字依据和图表行数，防止已发现的准确性问题再次出现。"""
from unittest.mock import patch
from decimal import Decimal
from uuid import uuid4
import matplotlib.axes
import pytest
from backend.core.storage import write_table, read_artifact
from backend.execution.metric_scope import scoped_query
from backend.reporting.verification import verify_report_inputs, numbers
from backend.reporting.builder import build_report
from backend.tools.chart import create_chart
from backend.reporting.requirements import requested_columns, python_requested
from backend.agent.nodes.tools import resolve_table_reference
from backend.agent.nodes.verification import verify
import asyncio


def test_status_conflict_and_cte_scope():
    """检查冲突状态是否直接报错，以及同名 CTE 是否只限制其真实来源表，不递归改写自身。"""
    with pytest.raises(ValueError, match='cancelled'):
        scoped_query("SELECT * FROM orders WHERE status='delivered'", 'cancelled')
    query = scoped_query('WITH orders AS (SELECT * FROM public.orders) SELECT COUNT(*) FROM orders', 'cancelled')
    assert query.count(':analysis_valid_status') == 1
    assert 'FROM orders' in query
    assert 'EXISTS' in scoped_query('SELECT SUM(price) FROM order_items', 'delivered')


def test_supported_dates_and_values_not_arbitrary_claims():
    """检查合法日期和有依据的数字能否通过，并确认未知数字的错误会指出具体报告字段。"""
    task_id = str(uuid4())
    table = write_table(task_id, '已计算结果', ['item_amount', 'change_percent'], [{'item_amount': Decimal('6550'), 'change_percent': Decimal('-21.04')}])
    verify_report_inputs(task_id, [table], ['2017-11 相对2017-10下降21.04%，销售额6,550.00。'], '2017年10月与11月', '比较2017年10月与11月')
    with pytest.raises(ValueError, match=r'notes\[0\].*999'):
        verify_report_inputs(task_id, [table], ['销售额999。'])
    with pytest.raises(ValueError, match=r'1E\+9'):
        verify_report_inputs(task_id, [table], ['销售额1e9。'])
    assert numbers('2017-10-01') == {Decimal(2017), Decimal(10), Decimal(1)}


def test_chart_and_report_use_all_rows():
    """记录真实绘图的柱数，检查图表、文件信息和报告都包含来源表末尾的行。"""
    task_id = str(uuid4())
    rows = [{'customer_id': f'customer-{index}', 'item_amount': index + 1} for index in range(66)]
    table = write_table(task_id, '客户金额', ['customer_id', 'item_amount'], rows)
    original = matplotlib.axes.Axes.barh
    drawn = []

    def capture(axis, positions, values, **kwargs):
        """调用实际绘图函数并记录输入柱数，不仅相信图表自己报告的行数。"""
        drawn.append(len(values))
        return original(axis, positions, values, **kwargs)

    with patch.object(matplotlib.axes.Axes, 'barh', capture):
        chart = create_chart(task_id, table['id'], 'customer_id', 'item_amount', '客户金额', currency='BRL')
    assert drawn == [66]
    assert chart['source_row_count'] == chart['plotted_row_count'] == 66
    assert chart['truncated'] is False
    verify_report_inputs(task_id, [table, chart], ['完整图表来自结果表。'])
    with pytest.raises(ValueError, match='完整覆盖'):
        verify_report_inputs(task_id, [table, {**chart, 'plotted_row_count': 30}], [])
    report = build_report({'task_id': task_id, 'question': '全部客户', 'mode': 'demo', 'metrics': {'currency': 'BRL', 'valid_status': 'delivered'},
                           'action': {'arguments': {'title': '全部客户报告', 'notes': []}}, 'artifacts': [table, chart]})
    _, path = read_artifact(report['id'])
    assert 'customer-65' in path.read_text(encoding='utf-8')
    assert '完整绘制 66 行' in path.read_text(encoding='utf-8')


def test_chart_too_large_or_repeated_dimensions_fails_explicitly():
    """检查数据过多或分类重复时是否明确报错，不允许自动截断或覆盖部分类别。"""
    task_id = str(uuid4())
    table = write_table(task_id, '大图', ['x', 'y'], [{'x': str(index), 'y': 1} for index in range(201)])
    with pytest.raises(ValueError, match='不会自动截取'):
        create_chart(task_id, table['id'], 'x', 'y', '大图')
    repeated = write_table(task_id, '重复维度', ['x', 'y'], [{'x': 'same', 'y': 1}, {'x': 'same', 'y': 2}])
    with pytest.raises(ValueError, match='重复值'):
        create_chart(task_id, repeated['id'], 'x', 'y', '重复')


def test_requested_delivery_and_local_reference():
    """检查格式说明不被当成列名，否定 Python 不强制调用，以及表格引用不能跨任务使用。"""
    assert requested_columns('结果列名 month（YYYY-MM）、item_amount、order_count，生成报告。') == {'month', 'item_amount', 'order_count'}
    assert requested_columns('结果列名 item_amount、order_count，之后用 Python 导出 CSV。') == {'item_amount', 'order_count'}
    assert python_requested('请使用 Python 计算百分比')
    assert not python_requested('无需使用 Python，用 SQL 即可')
    identifier = str(uuid4())
    state = {'artifacts': [{'id': identifier, 'kind': 'table'}]}
    assert resolve_table_reference(state, 'table_1') == identifier
    with pytest.raises(ValueError, match='table_1'):
        resolve_table_reference(state, str(uuid4()))


def test_status_narrative_matches_calculated_scope():
    """检查说明中的订单状态是否与实际查询一致，即使数字正确，也不能错误描述包含或排除的状态。"""
    task_id = str(uuid4())
    table = write_table(task_id, '取消订单', ['order_count'], [{'order_count': 6}], sql='SELECT COUNT(*) FROM orders',
                        metric_scope={'valid_status': 'cancelled', 'enforced': True})
    verify_report_inputs(task_id, [table], ['按有效状态 cancelled 统计，订单量6。'], expected_status='cancelled')
    with pytest.raises(ValueError, match='状态说明与实际查询冲突'):
        verify_report_inputs(task_id, [table], ['已排除 cancelled，订单量6。'], expected_status='cancelled')
    with pytest.raises(ValueError, match='错称已交付'):
        verify_report_inputs(task_id, [table], ['已交付订单量6。'], expected_status='cancelled')


@pytest.mark.parametrize('title', ['2017年10月-11月的销售额', '2017年10月—11月的销售额', '2017年10-11月的销售额'])
def test_month_range_not_negative_number(title):
    """检查合法月份范围不会被读成负数，同时日期仍须符合允许的依据。"""
    task_id = str(uuid4())
    table = write_table(task_id, '月份', ['month', 'amount'], [{'month': '2017-10', 'amount': 100}, {'month': '2017-11', 'amount': 90}])
    verify_report_inputs(task_id, [table], [], title)
    with pytest.raises(ValueError, match='无依据数字'):
        verify_report_inputs(task_id, [table], ['销售额下降-11。'])


def test_valid_results_survive_repeated_unsupported_notes(monkeypatch):
    """检查结果完整但说明数字连续错误时，最终报告是否只保留已计算结果和明确提示。"""
    async def event(*args):
        """用模拟函数替代事件写库，只检查校验失败后的报告处理规则。"""
        return None

    monkeypatch.setattr('backend.agent.nodes.verification.emit_event', event)
    task_id = str(uuid4())
    table = write_table(task_id, '金额', ['item_amount'], [{'item_amount': 100}])
    state = {'task_id': task_id, 'question': '结果列名 item_amount。', 'mode': 'demo', 'messages': [],
             'metrics': {'valid_status': 'delivered'}, 'artifacts': [table], 'verification_failures': 2,
             'action': {'name': 'finish', 'arguments': {'title': '金额', 'notes': ['销售额999。']}}}
    result = asyncio.run(verify(state))
    assert result['action']['verified'] is True
    assert '999' not in str(result['action']['arguments'])
    assert '仅交付' in str(result['action']['arguments'])
    assert result['report_warning']


def test_note_fallback_cannot_hide_incomplete_chart(monkeypatch):
    """检查删除无依据说明后，缺行的图表仍不能通过，报告生成前必须再次核对结果。"""
    async def event(*args):
        """替代事件写入函数，使本次单元测试不连接任务数据库。"""
        return None

    monkeypatch.setattr('backend.agent.nodes.verification.emit_event', event)
    task_id = str(uuid4())
    table = write_table(task_id, '金额', ['item_amount'], [{'item_amount': 100}])
    chart = {'kind': 'chart', 'source_id': table['id'], 'source_row_count': 1, 'plotted_row_count': 0, 'truncated': True}
    state = {'task_id': task_id, 'question': '查询金额并绘图', 'mode': 'demo', 'messages': [],
             'metrics': {'valid_status': 'delivered'}, 'artifacts': [table, chart], 'verification_failures': 2,
             'action': {'name': 'finish', 'arguments': {'title': '金额', 'notes': ['销售额999。']}}}
    with pytest.raises(ValueError, match='完整覆盖'):
        asyncio.run(verify(state))
