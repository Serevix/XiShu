"""验证框架工具在真实数据库与 checkpoint 中工作，独立手算指标并检查并发登记。"""
import asyncio
import json
from datetime import datetime
from decimal import Decimal
from uuid import uuid4
import httpx
import pytest
from sqlalchemy import select
from backend.core.database import Session
from backend.core.config import get_settings
from backend.core.storage import read_table
from backend.models import Order, Customer
from backend.repositories.analysis_tasks import register_artifact
from backend.tools.sales_metrics import sales_metrics
from backend.workers.tasks import analyze
from tests.integration.test_pipeline import workspace, ready_demo

pytestmark = pytest.mark.integration


def test_multi_call_roundtrip_with_real_checkpoint(workspace, monkeypatch):
    """同一模型响应的销售和复购均执行，真实 checkpoint 不保存运行时锁，报告保留两份结果。"""
    client, headers = workspace
    dataset = ready_demo(client, headers[0])
    monkeypatch.setattr(get_settings(), 'model_api_key', 'offline-batch-token')
    def response(request):
        """首次返回两个工具，第二次确认两份反馈再完成，整个模型接口使用离线传输。"""
        body = json.loads(request.content)
        results = [item for item in body['messages'] if item['role'] == 'tool']
        if not results:
            calls = [('sales_metrics', {}), ('customer_repeat_metrics', {})]
        else:
            assert len(results) == 2
            assert {item['tool_call_id'] for item in results} == {'call_0','call_1'}
            refs = [json.loads(item['content'])['artifact_ref'] for item in results]
            assert len(set(refs)) == 2
            calls = [('finish', {'title': '销售和复购报告', 'notes': ['结果来自固定业务指标计算。']})]
        tool_calls = [{'id': f'call_{index}', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}} for index, (name, args) in enumerate(calls)]
        return httpx.Response(200, json={'choices': [{'message': {'role':'assistant','content':None,'tool_calls':tool_calls}}], 'usage': {'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}})
    monkeypatch.setattr('backend.agent.model.create_http_client', lambda: httpx.AsyncClient(transport=httpx.MockTransport(response)))
    task = client.post('/api/analysis-tasks', headers=headers[0], json={'dataset_id':dataset,'question':'汇总有效订单销售与独立客户复购','mode':'llm'}).json()
    asyncio.run(analyze(task['id']))
    result = client.get('/api/analysis-tasks/' + task['id'], headers=headers[0]).json()
    assert result['status'] == 'succeeded', result.get('error')
    tables = [item for item in result['artifacts'] if item['kind'] == 'table']
    assert {item['metric_id'] for item in tables} == {'sales_summary','customer_repeat'}
    assert {item['artifact_ref'] for item in tables} == {'table_1','table_2'}
    summary = next(item for item in tables if item['metric_id']=='sales_summary')
    row = read_table(summary['id'], task['id'])['rows'][0]
    assert row['order_count'] == 66 and Decimal(row['item_amount']) == Decimal('6550.00')
    assert result['usage']['total_tokens'] == 30
    assert any(event['type'] == 'tool_batch_started' for event in result['events'])


def test_sales_metrics_item_freight_and_orders_without_items(workspace):
    """真实 SQL 必须累加每项运费，并保留没有明细的有效订单，不能靠同一 SQL 生成预期值。"""
    client, headers = workspace
    dataset = ready_demo(client, headers[0])
    async def replace_with_known_orders():
        """在隔离测试数据集中只保留手工订单有效，原有订单变为其他状态。"""
        from sqlalchemy import update
        from backend.models import OrderItem, Product
        async with Session.begin() as session:
            await session.execute(update(Order).where(Order.dataset_id == dataset).values(status='inactive'))
            customer = await session.scalar(select(Customer.customer_id).where(Customer.dataset_id==dataset).limit(1))
            product = await session.scalar(select(Product.product_id).where(Product.dataset_id==dataset).limit(1))
            session.add_all([Order(dataset_id=dataset,order_id='known',customer_id=customer,status='delivered',purchase_time=datetime(2017,10,1)),
                             Order(dataset_id=dataset,order_id='empty',customer_id=customer,status='delivered',purchase_time=datetime(2017,11,1))])
            await session.flush()
            session.add_all([OrderItem(dataset_id=dataset,order_id='known',order_item_id=1,product_id=product,price=Decimal('100'),freight_value=Decimal('10')),
                             OrderItem(dataset_id=dataset,order_id='known',order_item_id=2,product_id=product,price=Decimal('200'),freight_value=Decimal('20'))])
    asyncio.run(replace_with_known_orders())
    task_id = str(uuid4())
    result = asyncio.run(sales_metrics(task_id,dataset,'delivered'))
    row = read_table(result['id'],task_id)['rows'][0]
    assert row['order_count'] == 2
    assert Decimal(row['item_amount']) == Decimal('300')
    assert Decimal(row['freight_amount']) == Decimal('30')
    assert Decimal(row['total_amount']) == Decimal('330')
    assert Decimal(row['aov']) == Decimal('150')
    monthly = asyncio.run(sales_metrics(task_id,dataset,'delivered',group_by='month'))
    rows = read_table(monthly['id'],task_id)['rows']
    assert len(rows) == 2 and rows[1]['order_count'] == 1 and Decimal(rows[1]['item_amount']) == 0
    assert rows[0]['amount_growth'] is None and Decimal(rows[1]['amount_growth']) == -100


def test_parallel_artifact_registration_assigns_unique_aliases(workspace):
    """并发事务分配引用不冲突，同一 ID 重复登记返回同一别名，取消后禁止登记。"""
    client, headers = workspace
    dataset = ready_demo(client,headers[0])
    task = client.post('/api/analysis-tasks',headers=headers[0],json={'dataset_id':dataset,'question':'测试并发登记','mode':'demo'}).json()
    artifacts = [{'id':str(uuid4()),'kind':'table'} for _ in range(6)]
    async def register_all():
        """同时提交登记请求，检测真实 PostgreSQL 行锁而不是模拟执行顺序。"""
        return await asyncio.gather(*(register_artifact(task['id'],item) for item in artifacts))
    result = asyncio.run(register_all())
    assert {item['artifact_ref'] for item in result} == {f'table_{index}' for index in range(1,7)}
    again = asyncio.run(register_artifact(task['id'],artifacts[0]))
    assert again['artifact_ref'] == result[0]['artifact_ref']
    assert client.post('/api/analysis-tasks/' + task['id'] + '/cancel',headers=headers[0]).status_code == 200
    with pytest.raises(InterruptedError):
        asyncio.run(register_artifact(task['id'],{'id':str(uuid4()),'kind':'table'}))


def test_standard_monthly_chart_and_final_selection(workspace, monkeypatch):
    """一次标准指标调用同时统计绘图，finish 选择完整表图并标记最终交付，不依赖额外 SQL。"""
    client, headers = workspace
    dataset = ready_demo(client,headers[0])
    monkeypatch.setattr(get_settings(),'model_api_key','offline-monthly-token')
    def response(request):
        """初次请求联合绘图，收到真实图表 ID 后明确选择最终表图。"""
        body=json.loads(request.content)
        results=[item for item in body['messages'] if item['role']=='tool']
        if not results:
            name,args='sales_metrics',{'group_by':'month','chart_x':'month','chart_y':'item_amount','chart_title':'月度销售额','chart_kind':'line'}
        else:
            result=json.loads(results[-1]['content'])
            name,args='finish',{'title':'月度报告','notes':['结果来自标准指标工具。'],'result_refs':[result['artifact_ref'],result['chart']['id']]}
        call={'id':f'call_{len(results)}','type':'function','function':{'name':name,'arguments':json.dumps(args)}}
        return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[call]}}]})
    monkeypatch.setattr('backend.agent.model.create_http_client',lambda: httpx.AsyncClient(transport=httpx.MockTransport(response)))
    task=client.post('/api/analysis-tasks',headers=headers[0],json={'dataset_id':dataset,'mode':'llm','question':'按月统计并绘制折线图'}).json()
    asyncio.run(analyze(task['id']))
    result=client.get('/api/analysis-tasks/'+task['id'],headers=headers[0]).json()
    assert result['status']=='succeeded',result.get('error')
    assert len([e for e in result['events'] if e['type']=='decision'])==2
    assert all(item['delivery_role']=='final' for item in result['artifacts'])
    table=next(item for item in result['artifacts'] if item['kind']=='table')
    rows=read_table(table['id'],task['id'])['rows']
    assert Decimal(rows[1]['amount_growth'])==Decimal('-21.04')
