"""使用真实 PostgreSQL 和 Milvus 检查记忆、会话与用户隔离，嵌入和摘要接口使用模拟响应。"""
import asyncio
import json
from uuid import uuid4
from sqlalchemy import select
from backend.core.config import get_settings
from backend.core.database import Session
from backend.models import AnalysisTask, QuestionPattern, Conversation, ConversationMessage
from backend.context.conversations import conversation_context, finish_conversation
from backend.memory.service import learn_task, synchronize, personalize
from backend.memory.vectors import write_vector, search_vectors, delete_vector
from backend.workers.tasks import analyze
from tests.integration.test_pipeline import workspace, ready_demo
import pytest

pytestmark = pytest.mark.integration


def create(client, headers, dataset, question='按月统计金额并绘图', conversation=None):
    """通过任务接口创建测试任务，可以指定已有会话以继续多轮分析。"""
    response = client.post('/api/analysis-tasks', headers=headers, json={'dataset_id':dataset,'question':question,'mode':'demo','conversation_id':conversation})
    assert response.status_code == 201, response.text
    return response.json()


def owner(client, headers):
    """用测试令牌查询真实用户 ID，不把令牌打印或写入结果。"""
    return client.get('/api/auth/me', headers=headers).json()['id']


def test_preferences_delivery_and_isolation(workspace, monkeypatch):
    """检查偏好是否影响图表和报告、本次要求是否覆盖旧偏好，以及其他用户能否访问这些记录。"""
    client, headers = workspace; monkeypatch.setattr(get_settings(), 'memory_enabled', True)
    from backend.workers.tasks import update_task
    async def observe_terminal(task_id, **changes):
        """在任务成功状态提交时检查报告是否已经保存到会话，避免下一次提问缺少上一份报告。"""
        if changes.get('status') == 'succeeded':
            async with Session() as session:
                saved = await session.scalar(select(ConversationMessage).where(ConversationMessage.task_id == task_id, ConversationMessage.role == 'assistant'))
                assert saved and json.loads(saved.context_text)['report_completed']
                assert json.loads(saved.context_text)['charts'][0]['plotted_row_count'] > 0
        return await update_task(task_id, **changes)
    monkeypatch.setattr('backend.workers.tasks.update_task', observe_terminal)
    dataset = ready_demo(client, headers[0])
    saved = client.put('/api/memories/preferences', headers=headers[0], json={'chart_kind':'line','report_style':'concise'})
    assert saved.status_code == 200
    assert client.put('/api/memories/preferences', headers=headers[0], json={'valid_status':'cancelled'}).status_code == 422
    assert client.get('/api/memories', headers=headers[1]).json()['preferences'] == []
    identifier = saved.json()[0]['id']
    assert client.delete('/api/memories/preference/' + identifier, headers=headers[1]).status_code == 404
    task = create(client, headers[0], dataset)
    asyncio.run(analyze(task['id']))
    result = client.get('/api/analysis-tasks/' + task['id'], headers=headers[0]).json()
    assert result['status'] == 'succeeded', result
    events = [e for e in result['events'] if e['type'] == 'tool_started' and e['payload']['tool'] == 'render_chart']
    assert events[-1]['payload']['arguments']['kind'] == 'line'
    assert next(a for a in result['artifacts'] if a['kind'] == 'chart')['chart_kind'] == 'line'
    report = next(a for a in result['artifacts'] if a['kind'] == 'report')
    assert '说明风格：简洁' in client.get('/api/artifacts/' + report['id'], headers=headers[0]).text
    override = create(client, headers[0], dataset, '按月统计金额，本次画柱状图', task['conversation_id'])
    asyncio.run(analyze(override['id']))
    result = client.get('/api/analysis-tasks/' + override['id'], headers=headers[0]).json()
    events = [e for e in result['events'] if e['type'] == 'tool_started' and e['payload']['tool'] == 'render_chart']
    assert events[-1]['payload']['arguments']['kind'] == 'bar'
    assert next(a for a in result['artifacts'] if a['kind'] == 'chart')['chart_kind'] == 'bar'
    assert client.delete('/api/memories/preference/' + identifier, headers=headers[0]).status_code == 200
    assert 'chart_kind' not in asyncio.run(personalize(owner(client, headers[0]), '金额趋势'))['preferences']


def test_pattern_count_recall_version_and_forgetting(workspace, monkeypatch):
    """检查每个成功任务只学习一次，并拒绝已删除或版本不符的向量候选。"""
    async def embedded(content):
        """提供固定的合法向量，向量写入和检索仍通过真实 Milvus SDK 执行。"""
        return [1.0] + [0.0] * 1023
    client, headers = workspace; monkeypatch.setattr(get_settings(), 'memory_enabled', True)
    monkeypatch.setattr('backend.memory.service.embed', embedded)
    dataset = ready_demo(client, headers[0]); uid = owner(client, headers[0])
    tasks = [create(client, headers[0], dataset, '统计2017-10月金额'), create(client, headers[0], dataset, '统计2017-11月金额')]
    async def mark_and_learn():
        """把测试任务标记为成功后单独检查重复计数保护。这不表示任务运行过真实 Agent 分析。"""
        async with Session.begin() as session:
            for task in tasks:
                (await session.get(AnalysisTask, task['id'])).status = 'succeeded'
        for task in tasks:
            await learn_task(task['id']); await learn_task(task['id'])
        async with Session() as session:
            row = await session.scalar(select(QuestionPattern).where(QuestionPattern.user_id == uid))
            assert row.use_count == 2
            return row.id
    identifier = asyncio.run(mark_and_learn())
    try:
        asyncio.run(synchronize('pattern:' + identifier))
        result = asyncio.run(personalize(uid, '每月金额'))
        assert result['vector_status'] == 'searched' and result['patterns'][0]['id'] == identifier
        assert search_vectors(owner(client, headers[1]), asyncio.run(embedded('任意'))) == []
        async def bump_version():
            """修改记忆版本而不立即同步向量，检查旧向量是否被拒绝。"""
            async with Session.begin() as session:
                row = await session.get(QuestionPattern, identifier); row.version += 1; row.sync_status = 'pending'
        asyncio.run(bump_version())
        assert asyncio.run(personalize(uid, '每月金额'))['patterns'] == []
        asyncio.run(synchronize('pattern:' + identifier))
        assert client.delete('/api/memories/pattern/' + identifier, headers=headers[0]).status_code == 200
        assert asyncio.run(personalize(uid, '每月金额'))['patterns'] == []
        fresh = create(client, headers[0], dataset, '统计2017-12月金额')
        async def learn_forgotten():
            """检查已被用户忘记的模板不会因为后续同类问题自动恢复。"""
            async with Session.begin() as session:
                (await session.get(AnalysisTask, fresh['id'])).status = 'succeeded'
            await learn_task(fresh['id'])
        asyncio.run(learn_forgotten())
        assert client.get('/api/memories', headers=headers[0]).json()['patterns'] == []
    finally:
        delete_vector(identifier, uid)


def test_conversation_lock_history_summary_retry(workspace, monkeypatch):
    """检查会话归属、数据集一致性和原文保留，并验证摘要失败不更新进度、恢复后可以继续总结。"""
    async def broken(previous, entries):
        """模拟摘要请求超时，检查数据库中的摘要进度是否保持不变。"""
        raise TimeoutError('测试失败')
    async def recovered(previous, entries):
        """模拟摘要接口恢复，不调用实际收费模型。"""
        assert entries
        return '用户关注月度订单金额；日期需要按本次问题确定', {'total_tokens':20}
    client, headers = workspace; dataset = ready_demo(client, headers[0])
    task = create(client, headers[0], dataset); cid = task['conversation_id']
    assert client.get('/api/conversations/' + cid, headers=headers[1]).status_code == 404
    assert client.post('/api/analysis-tasks', headers=headers[0], json={'dataset_id':dataset,'question':'追问','mode':'demo','conversation_id':cid}).status_code == 409
    asyncio.run(analyze(task['id'])); asyncio.run(finish_conversation(task['id']))
    for index in range(2):
        next_task = create(client, headers[0], dataset, '按月统计金额', cid); asyncio.run(analyze(next_task['id']))
    next_task = create(client, headers[0], dataset, '那十一月呢', cid)
    monkeypatch.setattr(get_settings(), 'context_recent_messages', 2)
    monkeypatch.setattr('backend.context.conversations.summarize', broken)
    uid = owner(client, headers[0]); failed = asyncio.run(conversation_context(cid, uid, next_task['id'], 'llm'))
    assert failed['summary_cursor'] == 0
    page = client.get('/api/conversations/' + cid + '?limit=2', headers=headers[0]).json()
    assert page['last_sequence'] == 7 and len(page['messages']) == 2 and page['summary_error']
    monkeypatch.setattr('backend.context.conversations.summarize', recovered)
    succeeded = asyncio.run(conversation_context(cid, uid, next_task['id'], 'llm'))
    assert succeeded['summary_cursor'] == 4 and len(succeeded['messages']) == 2
    page = client.get('/api/conversations/' + cid, headers=headers[0]).json()
    assert len(page['messages']) == 7 and page['summary_error'] is None
    foreign_dataset = ready_demo(client, headers[0])
    assert client.post('/api/analysis-tasks', headers=headers[0], json={'dataset_id':foreign_dataset,'question':'追问','mode':'demo','conversation_id':cid}).status_code == 409


def test_milvus_owner_filter_delete():
    """检查不同用户使用相同向量时仍互相隔离，且不能删除对方记录。"""
    owner_a, owner_b = str(uuid4()), str(uuid4()); id_a, id_b = str(uuid4()), str(uuid4())
    vector = [1.0] + [0.0] * 1023
    try:
        for identifier, uid in ((id_a,owner_a), (id_b,owner_b)):
            write_vector({'id':identifier,'user_id':uid,'kind':'pattern','text':'测试模式','version':1,'vector':vector})
        delete_vector(id_a, owner_b)
        assert [str(row['id']) for row in search_vectors(owner_a, vector)] == [id_a]
        assert [str(row['id']) for row in search_vectors(owner_b, vector)] == [id_b]
        delete_vector(id_a, owner_a)
        assert search_vectors(owner_a, vector) == []
    finally:
        delete_vector(id_a, owner_a); delete_vector(id_b, owner_b)
