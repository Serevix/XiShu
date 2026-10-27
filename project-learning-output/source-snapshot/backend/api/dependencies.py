"""为接口提供当前用户和数据库会话等公共依赖。"""
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from backend.core.database import get_session
from backend.core.security import decode_token
from backend.models.user import User

bearer = HTTPBearer(auto_error=False)


async def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
                       session: AsyncSession = Depends(get_session)) -> User:
    """验证登录令牌并读取用户记录。令牌无效或对应用户不存在时，返回未登录错误。"""
    try:
        if credentials is None:
            raise ValueError('missing token')
        # 验证 JWT 后还要查用户表。确认登录身份后，仍需通过 owned_* 函数检查资源是否属于这个用户。
        user = await session.get(User, decode_token(credentials.credentials))
        if user is None:
            raise ValueError('unknown user')
        return user
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(401, '请先登录或重新登录') from None
