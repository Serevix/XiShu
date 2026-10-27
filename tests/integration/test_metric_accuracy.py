"""修改测试数据库内容后检查订单口径和复购计算，避免代码只对固定示例答案有效。"""
import asyncio
import json
from decimal import Decimal
from sqlalchemy import text
import pytest
from backend.core.database import Session
from backend.execution.sql_executor import execute_query
from backend.tools.customer_metrics import customer_repeat_metrics
from backend.agent.nodes.verification import verify
from backend.core.storage import read_table
from backend.agent.nodes.tools import execute_tool
from tests.integration.test_pipeline import workspace, ready_demo

pytestmark = pytest.mark.integration


def test_monthly_window_comparison(workspace):
    """在实际数据库执行月度比较，检查跨月差值和零分母处理，并确认状态及只读限制仍生效。"""
    client, headers = workspace
    dataset = ready_demo(client, headers[0])
    task = client.post('/api/analysis-tasks', headers=headers[0], json={'dataset_id': dataset, 'question': '比较月度变化', 'mode': 'demo'}).json()
    query = """WITH monthly AS (
        SELECT TO_CHAR(o.purchase_time,'YYYY-MM') AS month, SUM(i.price) AS amount
        FROM orders o JOIN order_items i ON o.dataset_id=i.dataset_id AND o.order_id=i.order_id
        GROUP BY 1
    ) SELECT month, amount, amount-LAG(amount) OVER (ORDER BY month) AS delta,
        ROUND(100.0*(amount-LAG(amount) OVER (ORDER BY month))/NULLIF(LAG(amount) OVER (ORDER BY month),0),2) AS change_percent,
        LEAD(amount) OVER (ORDER BY month) AS next_amount
        FROM monthly ORDER BY month"""
    artifact = asyncio.run(execute_query(task['id'], dataset, query, 'delivered'))
    first, second = read_table(artifact['id'], task['id'])['rows']
    assert first['delta'] is None and Decimal(first['next_amount']) == Decimal('2890')
    assert Decimal(second['delta']) == Decimal('-770')
    assert Decimal(second['change_percent']) == Decimal('-21.04')


def test_integer_ratio_and_nested_division(workspace):
    """检查数据库执行计数比例和嵌套除法时是否保留小数，不仅比较 SQL 文本。"""
    client, headers = workspace
    dataset = ready_demo(client, headers[0])
    task = client.post('/api/analysis-tasks', headers=headers[0], json={'dataset_id': dataset, 'question': '统计比例', 'mode': 'demo'}).json()
    query = '''SELECT COUNT(*)/100 AS ratio, (COUNT(*)/100)/2 AS nested_left,
        COUNT(*)/(100/2) AS nested_right, COUNT(*)/NULLIF(0,0) AS zero_ratio FROM orders'''
    artifact = asyncio.run(execute_query(task['id'], dataset, query, 'delivered'))
    row = read_table(artifact['id'], task['id'])['rows'][0]
    assert Decimal(row['ratio']) == Decimal('0.66')
    assert Decimal(row['nested_left']) == Decimal('0.33')
    assert Decimal(row['nested_right']) == Decimal('1.32')
    assert row['zero_ratio'] is None
    assert 'CAST' in artifact['sql']


def test_ambiguous_column_feedback_and_native_month(workspace):
    """检查列名歧义是否提供可修正的错误，以及 PostgreSQL 按月统计函数是否正确执行。"""
    client, headers = workspace; dataset = ready_demo(client, headers[0])
    task = client.post('/api/analysis-tasks', headers=headers[0], json={'dataset_id':dataset,'question':'按月统计','mode':'demo'}).json()
    state = {'task_id':task['id'],'dataset_id':dataset,'question':'按月统计','metrics':{'valid_status':'delivered','currency':'BRL'},
             'artifacts':[], 'mode':'llm','messages':[], 'action':{'name':'execute_sql','call_id':'ambiguous',
             'arguments':{'query':'SELECT COUNT(DISTINCT order_id) FROM orders o JOIN order_items i ON o.order_id=i.order_id AND o.dataset_id=i.dataset_id'}}}
    feedback = asyncio.run(execute_tool(state))
    assert 'ambiguous' in json.loads(feedback['messages'][-1]['content'])['error']
    result = asyncio.run(execute_query(task['id'],dataset,"SELECT DATE_TRUNC('month',o.purchase_time) AS month, COUNT(DISTINCT o.order_id) AS n FROM orders o JOIN order_items i ON o.order_id=i.order_id AND o.dataset_id=i.dataset_id GROUP BY 1 ORDER BY 1",'delivered'))
    assert result['row_count'] == 2 and all(row['n'] == 33 for row in result['preview'])


def test_order_and_item_sources_enforce_confirmed_status(workspace):
    """检查直接查订单、直接查明细和使用同名 CTE 时，都仍受确认的订单状态限制。"""
    client, headers = workspace
    dataset = ready_demo(client, headers[0])
    task = client.post('/api/analysis-tasks', headers=headers[0], json={'dataset_id': dataset, 'question': '分析有效订单', 'mode': 'demo'}).json()
    for query in ('SELECT COUNT(*) AS n FROM orders', 'WITH orders AS (SELECT * FROM orders) SELECT COUNT(*) AS n FROM orders'):
        result = asyncio.run(execute_query(task['id'], dataset, query, 'cancelled'))
        assert result['preview'][0]['n'] == 6
    items = asyncio.run(execute_query(task['id'], dataset, 'SELECT SUM(price) AS amount FROM order_items', 'cancelled'))
    assert Decimal(items['preview'][0]['amount']) == Decimal('620')
    with pytest.raises(ValueError, match='状态与已确认口径冲突'):
        asyncio.run(execute_query(task['id'], dataset, "SELECT COUNT(*) FROM orders WHERE status='delivered'", 'cancelled'))


def test_customer_repeat_varied_ids_dates_and_status(workspace):
    """修改独立客户标识后检查复购答案是否随数据变化，同时检查日期和状态限制。"""
    client, headers = workspace
    dataset = ready_demo(client, headers[0])
    task = client.post('/api/analysis-tasks', headers=headers[0], json={'dataset_id': dataset, 'question': '统计客户复购', 'mode': 'demo'}).json()

    async def change_identity():
        """只修改本次测试的一条客户记录，把一位跨月客户拆成两位独立客户。"""
        async with Session.begin() as session:
            await session.execute(text("UPDATE customers SET customer_unique_id='new-independent-customer' WHERE dataset_id=:dataset AND customer_id='c-11-books-1'"), {'dataset': dataset})

    asyncio.run(change_identity())
    metric = asyncio.run(customer_repeat_metrics(task['id'], dataset, 'delivered'))
    row = read_table(metric['id'], task['id'])['rows'][0]
    assert row['unique_customers'] == 34 and row['repeat_customers'] == 32
    assert Decimal(row['repeat_rate']) == Decimal('94.12')
    october = asyncio.run(customer_repeat_metrics(task['id'], dataset, 'delivered', '2017-10-01', '2017-11-01'))
    assert october['preview'][0]['unique_customers'] == 33 and october['preview'][0]['repeat_customers'] == 0
    cancelled = asyncio.run(customer_repeat_metrics(task['id'], dataset, 'cancelled'))
    assert cancelled['preview'][0]['unique_customers'] == 3 and cancelled['preview'][0]['repeat_customers'] == 3
    state = {'task_id': task['id'], 'mode': 'demo', 'messages': [], 'question': '统计客户复购', 'metrics': {'valid_status': 'delivered'},
             'action': {'name': 'finish', 'arguments': {'title': '客户复购报告', 'notes': []}}, 'artifacts': []}
    rejected = asyncio.run(verify(state))
    assert rejected['action']['verified'] is False
    accepted = asyncio.run(verify({**state, 'artifacts': [metric]}))
    assert accepted['action']['verified'] is True


def test_combined_query_chart_and_short_reference(workspace):
    """检查一次调用能否完成 SQL 查询和绘图，并使用本任务短引用继续在沙箱中执行 Python。"""
    client, headers = workspace
    dataset = ready_demo(client, headers[0])
    task = client.post('/api/analysis-tasks', headers=headers[0], json={'dataset_id': dataset, 'question': '按月统计', 'mode': 'demo'}).json()
    state = {'task_id': task['id'], 'dataset_id': dataset, 'metrics': {'valid_status': 'delivered', 'currency': 'BRL'},
             'artifacts': [], 'mode': 'llm', 'messages': [], 'action': {'name': 'execute_sql', 'call_id': 'query',
             'arguments': {'query': "SELECT TO_CHAR(purchase_time,'YYYY-MM') AS month,COUNT(*) AS n FROM orders GROUP BY 1 ORDER BY 1",
                           'chart_x': 'month', 'chart_y': 'n', 'chart_title': '订单趋势', 'chart_kind': 'line'}}}
    queried = asyncio.run(execute_tool(state))
    assert [item['kind'] for item in queried['artifacts']] == ['table', 'chart']
    assert queried['artifacts'][0]['artifact_ref'] == 'table_1'
    assert queried['artifacts'][1]['plotted_row_count'] == 2
    calculated = asyncio.run(execute_tool({**state, **queried, 'action': {'name': 'run_python', 'call_id': 'python',
                      'arguments': {'artifact_id': 'table_1', 'code': 'result = df.copy()\nresult["double"] = result["n"] * 2'}}}))
    assert calculated['artifacts'][-1]['artifact_ref'] == 'table_2'
    assert calculated['artifacts'][-1]['preview'][0]['double'] == 66


def test_incomplete_report_cannot_drop_requested_fields_or_python(workspace):
    """检查缺少指定列、跳过 Python 要求或反复不修正错误的任务，是否仍会被拒绝交付。"""
    client, headers = workspace
    dataset = ready_demo(client, headers[0])
    task = client.post('/api/analysis-tasks', headers=headers[0], json={'dataset_id': dataset, 'question': '计算指标', 'mode': 'demo'}).json()
    partial = asyncio.run(execute_query(task['id'], dataset, 'SELECT SUM(price) AS previous_amount FROM order_items', 'delivered'))
    state = {'task_id': task['id'], 'mode': 'demo', 'messages': [], 'question': '结果表列名 previous_amount、delta_amount。',
             'metrics': {'valid_status': 'delivered'}, 'artifacts': [partial],
             'action': {'name': 'finish', 'arguments': {'title': '报告', 'notes': []}}}
    assert asyncio.run(verify(state))['action']['verified'] is False
    python_state = {**state, 'question': '使用 Python 计算结果，列名 previous_amount。'}
    assert asyncio.run(verify(python_state))['action']['verified'] is False
    with pytest.raises(ValueError, match='三次仍失败'):
        asyncio.run(verify({**state, 'verification_failures': 2}))
