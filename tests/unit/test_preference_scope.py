"""检查只保存用户明确的长期偏好，不误保存本次要求或否定表达。"""
import asyncio
import pytest
from backend.core.config import get_settings
from backend.memory.service import explicit_preferences, resolve_personalization


@pytest.mark.parametrize('question,expected', [
    ('以后默认用柱状图，但本次用折线图', {'chart_kind': 'bar'}),
    ('以后不要用折线图', {}),
    ('记住以后用折线图，说明简洁一些', {'chart_kind': 'line', 'report_style': 'concise'}),
])
def test_long_term_scope(question, expected, monkeypatch):
    """记录偏好写入参数，检查否定表达和本次要求不会被当成长期偏好保存。"""
    captured = {}

    async def save(user_id, updates):
        """只记录函数准备保存的偏好，不连接数据库或外部模型。"""
        captured.update(updates)

    monkeypatch.setattr(get_settings(), 'memory_enabled', True)
    monkeypatch.setattr('backend.memory.service.set_preferences', save)
    asyncio.run(explicit_preferences('test-owner', question))
    assert captured == expected


def test_current_scope_and_negation():
    """检查本次明确要求优先，并且否定某类图表不会被误解为选用该图表。"""
    assert resolve_personalization('以后默认用柱状图，但本次用折线图', {'chart_kind': 'bar'}) == {'chart_kind': 'line'}
    assert resolve_personalization('本次不要用折线图', {'chart_kind': 'line'}) == {}
