"""检查说明反复出错时能否只交付已检查的结果，并确保错误数字不会留在报告中。"""
import asyncio
from backend.agent.model import demo_action
from backend.workers.tasks import analyze
from tests.integration.test_pipeline import workspace, ready_demo
import pytest

pytestmark = pytest.mark.integration


def test_fact_report_fallback_through_checkpoint(workspace, monkeypatch):
    """让模型说明连续包含无依据数字，检查完整流程能否生成带明确提示的计算结果报告。"""
    def invalid_explanation(state):
        """保留真实查询、Python 和绘图流程，仅让最终说明反复包含未计算的数字。"""
        action = demo_action(state)
        if action['name'] == 'finish':
            return {**action, 'arguments': {**action['arguments'], 'notes': ['未经计算的销售额999999999。']}}
        return action

    client, headers = workspace
    dataset = ready_demo(client, headers[0])
    monkeypatch.setattr('backend.agent.nodes.decision.demo_action', invalid_explanation)
    task = client.post('/api/analysis-tasks', headers=headers[0], json={
        'dataset_id': dataset, 'question': '按月统计金额并绘图', 'mode': 'demo'}).json()
    asyncio.run(analyze(task['id']))
    result = client.get('/api/analysis-tasks/' + task['id'], headers=headers[0]).json()
    assert result['status'] == 'succeeded'
    assert any(event['type'] == 'report_fallback' for event in result['events'])
    report = next(artifact for artifact in result['artifacts'] if artifact['kind'] == 'report')
    content = client.get('/api/artifacts/' + report['id'], headers=headers[0]).text
    assert '仅交付' in content and '999999999' not in content
    assert '来源 ID' in content and '完整绘制' in content
