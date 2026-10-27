"""使用 Argon2 保存密码哈希，并使用有期限的 JWT 登录令牌识别用户。"""

from datetime import datetime, timedelta, timezone
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from backend.core.config import get_settings

hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """为密码生成包含随机盐的 Argon2 哈希。数据库保存哈希，不保存原始密码。"""
    return hasher.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    """核对密码与已保存的哈希。密码不匹配或哈希无法验证时，返回 False。"""
    try:
        return hasher.verify(encoded, password)
    except VerificationError:
        return False


def create_token(user_id: str) -> str:
    """生成有效期为十二小时、仅供本应用使用的登录令牌。"""
    return jwt.encode({'sub': user_id, 'aud': 'agent-analytics',
                       'exp': datetime.now(timezone.utc) + timedelta(hours=12)},
                      get_settings().jwt_secret, algorithm='HS256')


def decode_token(token: str) -> str:
    """检查令牌的签名、有效期和目标应用，验证通过后返回用户 ID。"""
    data = jwt.decode(token, get_settings().jwt_secret,
                      algorithms=['HS256'], audience='agent-analytics')
    return data['sub']
