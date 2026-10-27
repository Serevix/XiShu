"""用真实数据库和演示流程复现任务结束时的事件读取与取消竞争。"""
import asyncio
from backend.core.database import Session
from backend.models import AnalysisTask
from backend.workers.tasks import analyze
from tests.integration.test_pipeline import workspace, ready_demo
import pytest

pytestmark = pytest.mark.integration


def test_terminal_event_visible_before_memory_learning(workspace, monkeypatch):
    """检查记忆学习开始前，成功状态和完成事件是否已经一起提交，避免事件流漏读。"""
    client, headers = workspace
    dataset = ready_demo(client, headers[0])
    task = client.post('/api/analysis-tasks', headers=headers[0], json={
        'dataset_id': dataset, 'question': '统计订单', 'mode': 'demo'}).json()
    observed = []

    async def inspect_terminal(task_id):
        """在报告交付和记忆处理之间，读取数据库中的任务状态与事件。"""
        async with Session() as session:
            row = await session.get(AnalysisTask, task_id)
            observed.append((row.status, [event['type'] for event in row.events]))

    monkeypatch.setattr('backend.workers.tasks.learn_task', inspect_terminal)
    asyncio.run(analyze(task['id']))
    assert observed and observed[0][0] == 'succeeded'
    assert 'completed' in observed[0][1]
    stream = client.get('/api/analysis-tasks/' + task['id'] + '/events', headers=headers[0])
    assert 'completed' in stream.text


def test_cancel_at_delivery_does_not_emit_completed(workspace, monkeypatch):
    """在报告保存后、任务成功状态提交前取消任务，检查 Worker 是否仍错误地写入完成事件。"""
    client, headers = workspace
    dataset = ready_demo(client, headers[0])
    task = client.post('/api/analysis-tasks', headers=headers[0], json={
        'dataset_id': dataset, 'question': '统计订单', 'mode': 'demo'}).json()

    async def cancel_at_delivery(task_id, **kwargs):
        """在确定的位置执行取消事务，模拟最终提交前的取消，不依赖不稳定的时间等待。"""
        async with Session.begin() as session:
            row = await session.get(AnalysisTask, task_id, with_for_update=True)
            row.status = 'cancelled'

    monkeypatch.setattr('backend.workers.tasks.finish_conversation', cancel_at_delivery)
    asyncio.run(analyze(task['id']))
    result = client.get('/api/analysis-tasks/' + task['id'], headers=headers[0]).json()
    assert result['status'] == 'cancelled'
    assert not any(event['type'] in ('completed', 'failed') for event in result['events'])
