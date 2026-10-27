"""通过模拟 HTTP 响应检查真实模型适配器的协议，不请求实际收费模型。"""
import asyncio
from functools import partial
from unittest.mock import patch
import httpx
from backend.agent.model import model_action
from backend.core.config import get_settings
from backend.agent.nodes.tools import feedback_messages
import json
import pytest


def test_real_adapter_tool_protocol(monkeypatch):
    """检查请求包含工具说明，并能解析返回的工具参数和 token 用量。"""
    monkeypatch.setattr(get_settings(), 'model_api_key', 'local-test-token')
    real_client = httpx.AsyncClient

    def respond(request):
        """模拟远程模型返回一次符合格式的工具调用。"""
        assert request.headers['authorization'] == 'Bearer local-test-token'
        return httpx.Response(200, json={'choices':[{'message':{'role':'assistant','content':None,
            'tool_calls':[{'id':'call_1','type':'function','function':{'name':'execute_sql',
                           'arguments':'{"query":"SELECT COUNT(*) FROM orders"}'}}],
                           'reasoning_content':'local protocol field'}}], 'usage':{'total_tokens':100}})

    with patch('backend.agent.model.create_http_client', partial(real_client, transport=httpx.MockTransport(respond))):
        action, messages, usage = asyncio.run(model_action({'messages':[{'role':'user','content':'统计订单'}]}))
    assert action['arguments']['query'] == 'SELECT COUNT(*) FROM orders'
    assert messages[-1]['tool_calls'][0]['id'] == 'call_1'
    assert messages[-1]['reasoning_content'] == 'local protocol field'
    assert usage['total_tokens'] == 100


def test_large_tool_feedback_preserves_json():
    """检查长工具反馈缩减后仍是完整 JSON，且文件引用 ID 可以解析。"""
    state = {'mode':'llm','messages':[], 'action':{'call_id':'call'}}
    body = {'id':'table-id','kind':'table','columns':['x'],'row_count':1,'preview':[{'x':'long' * 3000}]}
    result = feedback_messages(state, body)
    assert json.loads(result[0]['content'])['id'] == 'table-id'
    assert len(result[0]['content']) < 6000


@pytest.mark.parametrize('recover', [True,False])
def test_plain_answer_requires_bounded_tool_repair(monkeypatch, recover):
    """检查模型只返回文字时会被纠正，最多请求三次，并正确累计用量，不接受编造数字。"""
    monkeypatch.setattr(get_settings(),'model_api_key','local-protocol-repair')
    real_client = httpx.AsyncClient; requests = []
    def respond(request):
        """先返回无依据文字，再按场景返回正确工具调用或持续返回不符合协议的文字。"""
        body=json.loads(request.content); requests.append(body)
        if len(requests) > 1:
            assert body['messages'][-1]['role'] == 'user' and '不得直接复用历史答案' in body['messages'][-1]['content']
        message = {'role':'assistant','content':'未经查询的销售额999999'}
        if recover and len(requests) == 2:
            message['tool_calls'] = [{'id':'repaired','type':'function','function':{'name':'execute_sql','arguments':'{"query":"SELECT COUNT(*) FROM orders"}'}}]
            message['content'] = None
        return httpx.Response(200,json={'choices':[{'message':message}],'usage':{'total_tokens':20}})
    with patch('backend.agent.model.create_http_client',partial(real_client,transport=httpx.MockTransport(respond))):
        if recover:
            action,messages,usage=asyncio.run(model_action({'messages':[{'role':'user','content':'那十月呢'}]}))
            assert action['name'] == 'execute_sql' and usage['total_tokens'] == 40 and usage['protocol_repairs'] == 1
            assert '999999' not in str(messages)
        else:
            with pytest.raises(ValueError,match='连续未返回工具调用'):
                asyncio.run(model_action({'messages':[{'role':'user','content':'那十月呢'}]}))
    assert len(requests) == (2 if recover else 3)


@pytest.mark.parametrize('recover', [True, False])
def test_remote_disconnect_has_bounded_retries(monkeypatch, recover):
    """模拟评测中出现的远端断连，检查恢复后可成功，以及连续失败最多请求三次。"""
    monkeypatch.setattr(get_settings(), 'model_api_key', 'local-disconnect-test')
    real_client = httpx.AsyncClient
    attempts = []

    async def immediate_sleep(delay):
        """跳过测试中的等待时间，但不修改模型适配器实际使用的重试间隔。"""
        return None

    def respond(request):
        """首次请求模拟连接中断，之后按测试场景恢复正常或继续中断。"""
        attempts.append(request)
        if not recover or len(attempts) == 1:
            raise httpx.RemoteProtocolError('peer closed connection', request=request)
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': None,
            'tool_calls': [{'id': 'recovered', 'type': 'function', 'function': {'name': 'execute_sql',
                           'arguments': '{"query":"SELECT COUNT(*) FROM orders"}'}}]}}], 'usage': {'total_tokens': 20}})

    monkeypatch.setattr('backend.agent.model.asyncio.sleep', immediate_sleep)
    with patch('backend.agent.model.create_http_client', partial(real_client, transport=httpx.MockTransport(respond))):
        if recover:
            action, messages, usage = asyncio.run(model_action({'messages': [{'role': 'user', 'content': '统计订单'}]}))
            assert action['name'] == 'execute_sql' and usage['http_attempts'] == 2
        else:
            with pytest.raises(ValueError, match='有限重试后失败'):
                asyncio.run(model_action({'messages': [{'role': 'user', 'content': '统计订单'}]}))
    assert len(attempts) == (2 if recover else 3)
