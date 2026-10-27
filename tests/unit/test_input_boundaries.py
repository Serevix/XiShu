"""检查空白输入和澄清后的业务要求，防止创建无效任务或遗漏补充的统计口径。"""
import asyncio
from uuid import uuid4
import pytest
from pydantic import ValidationError
from backend.schemas.analysis_tasks import TaskRequest, ResumeRequest
from backend.core.storage import write_table
from backend.agent.nodes.verification import verify


@pytest.mark.parametrize('payload', [
    {'dataset_id': str(uuid4()), 'question': '   ', 'mode': 'llm'},
    {'dataset_id': str(uuid4()), 'question': '\n\t', 'mode': 'demo'},
])
def test_blank_question_rejected(payload):
    """检查只含空白的提问会被拒绝，不因为字符数达到下限就创建任务。"""
    with pytest.raises(ValidationError):
        TaskRequest.model_validate(payload)


def test_blank_clarification_rejected():
    """检查纯空白的补充答案不能恢复等待输入的任务。"""
    with pytest.raises(ValidationError):
        ResumeRequest(answer='  \n\t')


def test_clarified_repeat_uses_authoritative_metric(monkeypatch):
    """检查用户在澄清答案中增加复购要求时，也必须使用专用复购工具。"""
    async def record_event(*args):
        """替代数据库事件写入，只检查报告入口是否遵守业务要求。"""
        return None

    monkeypatch.setattr('backend.agent.nodes.verification.emit_event', record_event)
    task_id = str(uuid4())
    table = write_table(task_id, '未经权威复购工具计算', ['unique_customers', 'repeat_customers', 'repeat_rate'],
                        [{'unique_customers': 33, 'repeat_customers': 33, 'repeat_rate': 100}])
    state = {'task_id': task_id, 'question': '请帮我分析这份数据',
             'clarification_answer': '统计复购，结果列名 unique_customers、repeat_customers、repeat_rate。',
             'mode': 'demo', 'messages': [], 'metrics': {'valid_status': 'delivered'},
             'artifacts': [table], 'action': {'name': 'finish', 'arguments': {'title': '复购报告', 'notes': []}}}
    result = asyncio.run(verify(state))
    assert result['action']['verified'] is False


def test_clarified_dates_are_valid_report_evidence(monkeypatch):
    """检查用户在澄清中明确给出的日期可以写入报告，不被误判为没有依据的数字。"""
    async def record_event(*args):
        """替代事件写入，只检查澄清日期是否参与最终报告校验。"""
        return None

    monkeypatch.setattr('backend.agent.nodes.verification.emit_event', record_event)
    task_id = str(uuid4())
    table = write_table(task_id, '计算结果', ['item_amount'], [{'item_amount': 100}])
    state = {'task_id': task_id, 'question': '请统计商品金额',
             'clarification_answer': '按2017-11这个月份统计', 'mode': 'demo', 'messages': [],
             'metrics': {'valid_status': 'delivered'}, 'artifacts': [table],
             'action': {'name': 'finish', 'arguments': {'title': '金额报告', 'notes': ['按2017-11统计。']}}}
    assert asyncio.run(verify(state))['action']['verified'] is True
