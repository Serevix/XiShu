"""用本地模拟的模型响应检查完整模型模式，不向外部模型服务发送数据。"""
import asyncio
import json
from functools import partial
from unittest.mock import patch
from decimal import Decimal
import httpx
import pytest
from backend.core.config import get_settings
from backend.core.storage import read_table
from backend.workers.tasks import analyze
from tests.integration.test_pipeline import workspace, ready_demo

pytestmark = pytest.mark.integration


def test_llm_tool_roundtrip(workspace, monkeypatch):
    """检查模型适配器能否按 schema、SQL、finish 顺序执行，并正确记录调用 ID、用量和报告。"""
    client, headers = workspace
    dataset = ready_demo(client, headers[0])
    monkeypatch.setattr(get_settings(), 'model_api_key', 'local-protocol-test')
    original_client = httpx.AsyncClient

    def respond(request):
        """根据上一条工具结果生成模拟模型响应，返回符合接口协议的下一次工具调用。"""
        body = json.loads(request.content)
        names = [message['tool_calls'][0]['function']['name'] for message in body['messages'] if message.get('tool_calls')]
        if not names:
            name, arguments = 'get_dataset_schema', {}
        elif names[-1] == 'get_dataset_schema':
            assert body['messages'][-1]['role'] == 'tool'
            name, arguments = 'execute_sql', {'query':"SELECT COUNT(DISTINCT o.order_id) AS order_count, SUM(i.price) AS item_amount FROM orders o JOIN order_items i ON o.order_id=i.order_id AND o.dataset_id=i.dataset_id WHERE o.status='delivered'"}
        else:
            evidence = json.loads(body['messages'][-1]['content'])
            assert evidence['row_count'] == 1 and evidence['id']
            name, arguments = 'finish', {'title':'模型协议分析报告','notes':['结果来自实际数据库工具执行。']}
        call = {'id':'call_' + str(len(names)), 'type':'function','function':{'name':name,'arguments':json.dumps(arguments,ensure_ascii=False)}}
        return httpx.Response(200, json={'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[call]}}],
                                        'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}})

    task = client.post('/api/analysis-tasks', headers=headers[0], json={'dataset_id':dataset,'question':'统计有效订单和商品金额','mode':'llm'}).json()
    with patch('backend.agent.model.create_http_client', partial(original_client, transport=httpx.MockTransport(respond))):
        asyncio.run(analyze(task['id']))
    result = client.get('/api/analysis-tasks/' + task['id'], headers=headers[0]).json()
    assert result['status'] == 'succeeded', result
    table = next(item for item in result['artifacts'] if item.get('sql'))
    row = read_table(table['id'], task['id'])['rows'][0]
    assert row['order_count'] == 66 and Decimal(row['item_amount']) == Decimal('6550.00')
    assert result['usage']['total_tokens'] == 45
