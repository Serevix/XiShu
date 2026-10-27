"""检查登录令牌的签名、有效期和目标应用，不只验证注册登录的正常路径。"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import jwt
import pytest
from backend.core.config import get_settings
from backend.core.security import create_token, decode_token, hash_password, verify_password


def test_valid_token_and_password():
    """检查合法令牌和密码能够通过、错误密码不能通过，以及同一密码每次生成不同的加盐哈希。"""
    owner = str(uuid4())
    assert decode_token(create_token(owner)) == owner
    first, second = hash_password('test-only-password'), hash_password('test-only-password')
    assert first != second and verify_password('test-only-password', first)
    assert not verify_password('incorrect-password', first)


@pytest.mark.parametrize('kind', ['expired', 'wrong_audience', 'wrong_signature', 'unsigned'])
def test_invalid_tokens_rejected(kind):
    """检查过期、应用不符、签名错误和无签名的令牌都会被拒绝，不输出测试令牌。"""
    claims = {'sub': str(uuid4()), 'aud': 'agent-analytics',
              'exp': datetime.now(timezone.utc) + timedelta(minutes=5)}
    secret, algorithm = get_settings().jwt_secret, 'HS256'
    if kind == 'expired':
        claims['exp'] = datetime.now(timezone.utc) - timedelta(minutes=5)
    elif kind == 'wrong_audience':
        claims['aud'] = 'another-application'
    elif kind == 'wrong_signature':
        secret = 'independent-test-only-signature-value'
    else:
        secret, algorithm = '', 'none'
    token = jwt.encode(claims, secret, algorithm=algorithm)
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(token)
