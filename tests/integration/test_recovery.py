"""检查工具错误修正、进度恢复和达到步骤上限时的停止行为。"""
import asyncio
import pytest
from unittest.mock import patch
from sqlalchemy import select
from backend.models import AnalysisTask
from backend.core.database import Session
from backend.agent.model import demo_action
from backend.workers.tasks import analyze, recover_pending
from backend.core.config import get_settings
from tests.integration.test_pipeline import workspace, ready_demo

pytestmark = pytest.mark.integration


def test_tool_failure_then_repair(workspace, monkeypatch):
    """先让工具拒绝一次写入 SQL，再检查后续正确调用是否仍能完成分析。"""
    client, headers = workspace
    dataset = ready_demo(client, headers[0])
    attempted = False

    def faulty_then_valid(state):
        """第一次返回危险 SQL，后续通过模拟适配器提供修正后的调用。"""
        nonlocal attempted
        action = demo_action(state)
        if action['name'] == 'execute_sql' and not attempted:
            attempted = True
            return {'name':'execute_sql','arguments':{'query':'DELETE FROM orders'}}
        return action

    monkeypatch.setattr('backend.agent.nodes.decision.demo_action', faulty_then_valid)
    task = client.post('/api/analysis-tasks', headers=headers[0], json={'dataset_id':dataset,'question':'分析品类金额','mode':'demo'}).json()
    asyncio.run(analyze(task['id']))
    result = client.get('/api/analysis-tasks/' + task['id'], headers=headers[0]).json()
    assert result['status'] == 'succeeded', result
    assert any(event['type'] == 'tool_error' for event in result['events'])


def test_steps_budget_terminates(workspace, monkeypatch):
    """让工具持续失败，检查达到步骤上限后是否停止，而不是无限重试。"""
    client, headers = workspace
    dataset = ready_demo(client, headers[0])
    monkeypatch.setattr(get_settings(), 'max_agent_steps', 2)

    def always_invalid(state):
        """持续返回禁止访问系统表的查询，用于触发并检查步骤上限。"""
        return {'name':'execute_sql','arguments':{'query':'SELECT * FROM users'}}

    monkeypatch.setattr('backend.agent.nodes.decision.demo_action', always_invalid)
    task = client.post('/api/analysis-tasks', headers=headers[0], json={'dataset_id':dataset,'question':'分析品类金额','mode':'demo'}).json()
    asyncio.run(analyze(task['id']))
    result = client.get('/api/analysis-tasks/' + task['id'], headers=headers[0]).json()
    assert result['status'] == 'failed' and '最大分析步骤' in result['error']


def test_recovery_after_graph_complete(workspace):
    """模拟图已完成但任务状态尚未提交的情况，检查恢复时能否使用 checkpoint 而不重复分析。"""
    client, headers = workspace
    dataset = ready_demo(client, headers[0])
    task = client.post('/api/analysis-tasks', headers=headers[0], json={'dataset_id':dataset,'question':'分析品类金额','mode':'demo'}).json()
    asyncio.run(analyze(task['id']))
    before = client.get('/api/analysis-tasks/' + task['id'], headers=headers[0]).json()
    assert before['status'] == 'succeeded'

    async def simulate_lost_commit():
        """只修改本次任务，模拟保存图进度后、提交成功状态前进程崩溃的情况。"""
        async with Session.begin() as session:
            record = await session.get(AnalysisTask, task['id'])
            record.status = 'running'

    asyncio.run(simulate_lost_commit())
    with patch('backend.workers.tasks.celery_app.send_task') as sender:
        counts = asyncio.run(recover_pending())
        assert counts['tasks'] >= 1
        assert any(call.args[0] == 'analytics.analyze' and call.kwargs['args'] == [task['id']] for call in sender.call_args_list)
    asyncio.run(analyze(task['id']))
    after = client.get('/api/analysis-tasks/' + task['id'], headers=headers[0]).json()
    assert after['status'] == 'succeeded'
    assert len(after['artifacts']) == len(before['artifacts'])
