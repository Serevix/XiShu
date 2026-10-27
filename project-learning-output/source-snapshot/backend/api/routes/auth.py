"""提供本项目的注册、登录和当前用户查询接口。"""
import asyncio
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from backend.core.database import get_session
from backend.core.security import hash_password, verify_password, create_token
from backend.models.user import User
from backend.schemas.auth import Credentials
from backend.api.dependencies import current_user

router = APIRouter(prefix='/api/auth', tags=['认证'])


@router.post('/register', status_code=201)
async def register(payload: Credentials, session=Depends(get_session)):
    """创建用户名不重复的用户并签发登录令牌。用户名已存在时返回明确错误。"""
    user = User(username=payload.username, password_hash=await asyncio.to_thread(hash_password, payload.password))
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, '用户名已存在') from None
    return {'token': create_token(user.id), 'username': user.username}


@router.post('/login')
async def login(payload: Credentials, session=Depends(get_session)):
    """核对用户密码。用户名不存在和密码错误使用相同提示，避免泄露账号是否存在。"""
    user = await session.scalar(select(User).where(User.username == payload.username))
    if user is None or not await asyncio.to_thread(verify_password, payload.password, user.password_hash):
        raise HTTPException(401, '用户名或密码错误')
    return {'token': create_token(user.id), 'username': user.username}


@router.get('/me')
async def me(user=Depends(current_user)):
    """返回当前用户名，让前端确认保存的登录令牌仍然有效。"""
    return {'id': user.id, 'username': user.username}
