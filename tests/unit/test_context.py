"""不调用外部模型，检查上下文长度、工具消息配对和摘要进度更新。"""
import asyncio
import copy
import pytest
import httpx
from functools import partial
from unittest.mock import patch
from backend.core.config import get_settings
from backend.context.manager import prepare_context, safe_cut
from backend.context.summary import estimate_tokens
from backend.context.summary import summarize
from backend.memory.service import question_template, resolve_personalization


def long_state():
    """构造很长的旧工具消息和完整本次问题，检查压缩请求时是否误删原始历史。"""
    messages = [{'role':'system','content':'必须查询当前数据'}, {'role':'user','content':'旧问题'}]
    for index in range(8):
        messages.extend([{'role':'assistant','content':'历史分析' * 400, 'tool_calls':[{'id':str(index),'type':'function','function':{'name':'execute_sql','arguments':'{}'}}]},
                         {'role':'tool','tool_call_id':str(index),'content':'旧结果' * 400}])
    messages.append({'role':'user','content':'请完整保留本次问题，查询十一月金额'})
    return {'mode':'llm','question':messages[-1]['content'],'messages':messages,
            'artifacts':[{'id':'real-id','artifact_ref':'table_1','kind':'table','columns':['amount'],'preview':[{'amount':2890}]}]}


def assert_tool_pairs(messages):
    """检查每条 tool 结果都能找到保留下来的 assistant 调用，不能引用已经被裁掉的调用消息。"""
    calls = set()
    for item in messages:
        calls.update(call['id'] for call in item.get('tool_calls', []))
        if item['role'] == 'tool':
            assert item['tool_call_id'] in calls


def test_summary_preserves_history_and_current_question(monkeypatch):
    """检查摘要成功后进度前移、请求符合长度限制，同时保留 checkpoint 原文和结果引用。"""
    async def summary(previous, entries):
        """模拟摘要接口返回合法摘要和 token 用量。"""
        assert entries
        return '用户关注月度金额；旧结果需重新查询', {'total_tokens':10}
    monkeypatch.setattr('backend.context.manager.summarize', summary)
    monkeypatch.setattr(get_settings(), 'model_context_tokens', 6000)
    state = long_state(); original = copy.deepcopy(state['messages'])
    result = asyncio.run(prepare_context(state, []))
    assert result['summary_cursor'] > 1 and result['summary_usage']['total_tokens'] == 10
    assert state['messages'] == original
    assert any(item.get('content') == state['question'] for item in result['request_messages'])
    assert 'real-id' in str(result['request_messages'])
    assert estimate_tokens(result['request_messages']) <= 6000 - get_settings().model_max_tokens - estimate_tokens([]) - 500
    assert_tool_pairs(result['request_messages'])


def test_summary_failure_retains_cursor_and_retries(monkeypatch):
    """检查摘要失败时保留进度，下一轮接口恢复后能够再次总结并成功。"""
    async def broken(previous, entries):
        """模拟摘要模型接口超时。"""
        raise TimeoutError('测试超时')
    async def recovered(previous, entries):
        """模拟下一轮摘要接口恢复，检查只处理尚未成功总结的原文。"""
        return '恢复后的摘要', {'total_tokens':12}
    monkeypatch.setattr(get_settings(), 'model_context_tokens', 6000)
    monkeypatch.setattr('backend.context.manager.summarize', broken)
    state = long_state(); state['summary_cursor'] = 1
    failed = asyncio.run(prepare_context(state, []))
    assert 'summary_cursor' not in failed and failed['summary_error']
    assert_tool_pairs(failed['request_messages'])
    monkeypatch.setattr('backend.context.manager.summarize', recovered)
    state.update(failed)
    recovered_result = asyncio.run(prepare_context(state, []))
    assert recovered_result['summary_cursor'] > 1 and recovered_result['summary_error'] is None


def test_current_question_over_budget_is_explicit(monkeypatch):
    """检查完整本次问题无法放入请求时是否明确报错，不允许偷偷截断问题。"""
    monkeypatch.setattr(get_settings(), 'model_context_tokens', 4096)
    question = '本次重要问题' * 2000
    state = {'mode':'llm','question':question,'messages':[{'role':'system','content':'约束'}, {'role':'user','content':question}]}
    with pytest.raises(ValueError, match='未截断当前问题'):
        asyncio.run(prepare_context(state, []))
    assert state['messages'][-1]['content'] == question


@pytest.mark.parametrize('messages,recent,expected', [([],6,0), ([{'role':'system'}],6,1),
    ([{'role':'system'},{'role':'assistant'},{'role':'tool'},{'role':'user'}],2,1)])
def test_safe_cut_edges(messages, recent, expected):
    """检查空消息、只有系统消息和工具消息边界，不应越界或留下没有调用的结果消息。"""
    assert safe_cut(messages, recent) == expected


def test_question_pattern_and_preference_boundaries():
    """检查旧日期替换、敏感问题不学习、本次要求优先和偏好不改变统计口径。"""
    assert question_template('统计2017-10月金额') == question_template('统计2017-11月金额')
    assert question_template('我的api key是sk-secret') is None
    assert resolve_personalization('本次用柱状图，详细说明', {'chart_kind':'line','report_style':'concise','valid_status':'cancelled'}) == {'chart_kind':'bar','report_style':'detailed'}
    assert 'chart_kind' not in resolve_personalization('分别画折线图和柱状图', {'chart_kind':'bar'})


@pytest.mark.parametrize('content', ['用户关注十月商品金额，报告及图表已交付。', '{"history_fragment":"原文"}', '```json\n{}\n```'])
def test_summary_requires_rewritten_text(monkeypatch, content):
    """检查摘要接口直接返回 JSON 或原文包装时会被拒绝，不把这些内容当成成功摘要。"""
    monkeypatch.setattr(get_settings(),'model_api_key','local-summary-contract')
    real_client = httpx.AsyncClient
    def respond(request):
        """模拟摘要接口返回正常自然语言，或不符合要求的原文包装内容。"""
        return httpx.Response(200,json={'choices':[{'message':{'content':content}}],'usage':{'total_tokens':12}})
    with patch('backend.context.summary.httpx.AsyncClient',partial(real_client,transport=httpx.MockTransport(respond))):
        if content.startswith(('{','```')):
            with pytest.raises(ValueError,match='未推进摘要游标'):
                asyncio.run(summarize('',[{'role':'user','content':'十月销售额'}]))
        else:
            summary, usage = asyncio.run(summarize('',[{'role':'user','content':'十月销售额'}]))
            assert summary == content and usage['total_tokens'] == 12
