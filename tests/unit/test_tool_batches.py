"""通过真实 ToolNode 检查多调用、并发限额、错误隔离、控制动作和取消传播。"""
import asyncio
import json
import pytest
from backend.agent.nodes.tools import execute_tool
from backend.agent.model import model_action
from backend.core.config import get_settings
from backend.reporting.requirements import chart_requested, chart_forbidden
from backend.agent.tool_registry import TOOLS
from backend.reporting.verification import select_report_artifacts
import httpx


def batch_state(calls):
    """为每个动作建立原始工具调用消息，便于检查结果配对和完整保留。"""
    return {'task_id': 'test-task', 'mode': 'llm', 'messages': [], 'artifacts': [],
            'action': {'name': 'tool_batch', 'calls': [{'name': name, 'arguments': args, 'call_id': str(index)} for index, (name, args) in enumerate(calls)]}}


def setup_execution(monkeypatch, business):
    """只替换业务副作用，仍由真实 ToolNode 完成工具校验与调用。"""
    async def noop(*args, **kwargs):
        """离线测试不读写 PostgreSQL 状态或事件。"""
        return None
    monkeypatch.setattr('backend.agent.nodes.tools.check_cancelled', noop)
    monkeypatch.setattr('backend.agent.nodes.tools.emit_event', noop)
    monkeypatch.setattr('backend.agent.nodes.tools.execute_business', business)


def test_model_keeps_all_tool_calls(monkeypatch):
    """两个独立调用都进入 action 与 assistant 消息，没有取第一项丢弃其余项。"""
    monkeypatch.setattr(get_settings(), 'model_api_key', 'offline-token')
    def response(request):
        """在标准模型 HTTP 协议中返回两个合法调用。"""
        body = json.loads(request.content)
        assert 'sales_metrics' in [item['function']['name'] for item in body['tools']]
        calls = [{'id': str(index), 'type': 'function', 'function': {'name': name, 'arguments': '{}'}} for index, name in enumerate(['sales_metrics', 'customer_repeat_metrics'])]
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': calls}}], 'usage': {'prompt_tokens': 8, 'completion_tokens': 2, 'total_tokens': 10}})
    monkeypatch.setattr('backend.agent.model.create_http_client', lambda: httpx.AsyncClient(transport=httpx.MockTransport(response)))
    action, messages, usage = asyncio.run(model_action({'messages': [{'role': 'user', 'content': '销售与复购'}]}))
    assert len(action['calls']) == len(messages[-1]['tool_calls']) == 2
    assert usage['total_tokens'] == 10


def test_toolnode_runs_all_calls_with_bounded_concurrency(monkeypatch):
    """用屏障证明独立工具真实重叠执行，并确认最高并发和每个调用的反馈。"""
    active, highest, count = 0, 0, 0
    async def business(state):
        """记录同时执行数，模拟短暂计算，返回当前工具的独立结果。"""
        nonlocal active, highest, count
        active += 1; highest = max(highest, active)
        await asyncio.sleep(.02)
        active -= 1; count += 1
        return {'body': {'number': count}, 'artifacts': []}
    setup_execution(monkeypatch, business)
    monkeypatch.setattr(get_settings(), 'tool_concurrency', 2)
    result = asyncio.run(execute_tool(batch_state([('sales_metrics', {})] * 4)))
    assert highest == 2 and count == 4
    assert [item['tool_call_id'] for item in result['messages']] == ['0','1','2','3']


def test_bad_parameters_do_not_discard_successful_sibling(monkeypatch):
    """缺少必填 SQL 的调用被框架拒绝，其他工具仍执行并得到自己的反馈。"""
    async def business(state):
        """合法工具返回成功反馈，不创建任何真实文件。"""
        return {'body': {'ok': True}, 'artifacts': []}
    setup_execution(monkeypatch, business)
    result = asyncio.run(execute_tool(batch_state([('execute_sql', {}), ('sales_metrics', {})])))
    assert 'error' in json.loads(result['messages'][0]['content'])
    assert json.loads(result['messages'][1]['content'])['ok'] is True


def test_control_action_in_batch_has_feedback_for_every_call(monkeypatch):
    """结束与查询混批时整批拒绝，所有调用都收到配对错误，不偷偷提前交付。"""
    async def forbidden(state):
        """控制混批不应进入业务执行器。"""
        raise AssertionError('控制混批执行了业务工具')
    setup_execution(monkeypatch, forbidden)
    result = asyncio.run(execute_tool(batch_state([('sales_metrics', {}), ('finish', {'title':'x','notes':[]})])))
    assert len(result['messages']) == 2
    assert all('必须单独调用' in item['content'] for item in result['messages'])


def test_cancellation_drains_parallel_siblings(monkeypatch):
    """任一工具发现取消后，其余执行协程也停止并完成清理，不能留在后台继续工作。"""
    cleaned = []
    async def business(state):
        """第一个调用触发取消，第二个等待并在 finally 记录清理。"""
        if state['action']['call_id'] == '0':
            await asyncio.sleep(.02)
            raise InterruptedError('任务已取消')
        try:
            await asyncio.sleep(10)
        finally:
            cleaned.append(True)
    setup_execution(monkeypatch, business)
    with pytest.raises(InterruptedError):
        asyncio.run(execute_tool(batch_state([('sales_metrics', {})] * 2)))
    assert cleaned == [True]


def test_tools_hide_runtime_authorization_and_chart_requirements():
    """模型工具 schema 不含服务端上下文，明确与否定绘图要求按实际语义区分。"""
    for definition in TOOLS:
        assert not {'runtime','task_id','dataset_id','backend_state'} & set(definition['function']['parameters']['properties'])
    assert chart_requested('按月统计并画折线图')
    assert not chart_requested('统计总额，不用图表')
    assert not chart_requested('统计总额，不要绘图')
    assert chart_forbidden('统计总额，不要绘图')
    assert not chart_forbidden('不要柱状图，改用折线图')


def test_unknown_parameters_are_rejected_by_framework(monkeypatch):
    """未知数据集参数不能被框架默默忽略并执行，错误调用收到反馈且不进入业务层。"""
    async def forbidden(state):
        """参数校验失败时业务层不得执行。"""
        raise AssertionError('未知参数被执行')
    setup_execution(monkeypatch,forbidden)
    result=asyncio.run(execute_tool(batch_state([('execute_sql',{'query':'SELECT * FROM orders','dataset_id':'foreign'})])))
    assert 'error' in json.loads(result['messages'][0]['content'])


def test_explicit_no_chart_is_enforced_before_business_side_effects(monkeypatch):
    """用户禁图时在查询、文件和绘图副作用发生前拒绝联合绘图，不只靠模型提示。"""
    from backend.agent.nodes.tools import execute_business
    async def noop(*args,**kwargs):
        """离线替换数据库事件与取消检查。"""
        return None
    monkeypatch.setattr('backend.agent.nodes.tools.check_cancelled',noop)
    monkeypatch.setattr('backend.agent.nodes.tools.emit_event',noop)
    state={'task_id':'offline','mode':'llm','question':'不要绘图','artifacts':[],
           'action':{'name':'sales_metrics','call_id':'test','arguments':{'chart_x':'month','chart_y':'item_amount'}}}
    result=asyncio.run(execute_business(state))
    assert '明确不需要绘图' in result['body']['error'] and not result['artifacts']


def test_final_selection_keeps_chart_source_and_rejects_foreign_references():
    """最终结果可排除中间表，但图表来源仍须校验；他人或未知引用不能加入交付。"""
    artifacts=[{'id':'source','kind':'table','artifact_ref':'table_1'},
               {'id':'preview','kind':'table','artifact_ref':'table_2'},
               {'id':'chart','kind':'chart','source_id':'source'}]
    assert {item['id'] for item in select_report_artifacts(artifacts,['chart'])}=={'source','chart'}
    with pytest.raises(ValueError):
        select_report_artifacts(artifacts,['foreign'])
    with pytest.raises(ValueError):
        select_report_artifacts(artifacts,[])


def test_report_does_not_deliver_old_chart_after_user_forbids_drawing(monkeypatch):
    """已有图表也不能在用户新要求禁图后被最终报告选择，需要明确移出交付结果。"""
    from backend.agent.nodes.verification import verify
    async def noop(*args,**kwargs):
        """测试只核验交付条件，不读写事件数据库。"""
        return None
    monkeypatch.setattr('backend.agent.nodes.verification.emit_event',noop)
    state={'task_id':'offline','mode':'llm','question':'这次不要绘图','messages':[],
           'artifacts':[{'id':'old-chart','kind':'chart'}],
           'action':{'name':'finish','call_id':'call','arguments':{'title':'结果','notes':[]}}}
    result=asyncio.run(verify(state))
    assert result['action']['verified'] is False
    assert '最终结果不能选择图表' in result['messages'][-1]['content']
