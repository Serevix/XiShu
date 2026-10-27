"""检查配置读取、分类整理和不同模型平台的请求参数。"""
from pathlib import Path
import json
from dotenv import dotenv_values
from backend.core.config import get_settings
from backend.agent.model import model_parameters
from scripts.reformat_env import reformat_env


def test_reformat_preserves_credentials(tmp_path):
    """检查配置整理保留数据库凭据、认证密钥和自定义项，只修改指定字段。"""
    target = tmp_path / '.env'
    template = tmp_path / '.env.example'
    target.write_text('DATABASE_URL="postgresql://private"\nJWT_SECRET="test-private-value"\nCUSTOM="a#b"\nMODEL_API_KEY="old-value"\n', encoding='utf-8')
    template.write_text('# ----- 数据库 -----\nDATABASE_URL=placeholder\n# ----- 认证 -----\nJWT_SECRET=placeholder\n# ----- 模型 -----\nMODEL_API_KEY=\nMODEL_NAME=test-model\n', encoding='utf-8')
    reformat_env({'MODEL_API_KEY':'new-local-value'}, target, template)
    values = dotenv_values(target)
    assert values['DATABASE_URL'] == 'postgresql://private'
    assert values['JWT_SECRET'] == 'test-private-value'
    assert values['CUSTOM'] == 'a#b'
    assert values['MODEL_API_KEY'] == 'new-local-value'
    assert '# ----- 数据库 -----' in target.read_text(encoding='utf-8')


def test_siliconflow_parameters(monkeypatch):
    """检查硅基流动请求使用配置中的生成参数，并包含对应的思考开关。"""
    settings = get_settings()
    monkeypatch.setattr(settings, 'model_provider', 'siliconflow')
    monkeypatch.setattr(settings, 'model_max_tokens', 2048)
    monkeypatch.setattr(settings, 'model_temperature', 0.2)
    monkeypatch.setattr(settings, 'model_enable_thinking', False)
    body = model_parameters()
    assert body['extra_body']['enable_thinking'] is False
    assert body['max_tokens'] == 2048 and body['temperature'] == 0.2
    assert 'model_api_key' not in body


def test_other_providers_do_not_receive_siliconflow_fields(monkeypatch):
    """检查其他平台请求不包含硅基流动专用字段，避免发送不支持的参数。"""
    monkeypatch.setattr(get_settings(), 'model_provider', 'compatible')
    assert 'enable_thinking' not in model_parameters()['extra_body']


def test_settings_hide_secrets():
    """检查配置对象的调试文本不包含数据库地址、JWT 密钥或模型密钥。"""
    settings = get_settings()
    representation = repr(settings)
    assert settings.database_url not in representation
    assert settings.jwt_secret not in representation
    if settings.model_api_key:
        assert settings.model_api_key not in representation
