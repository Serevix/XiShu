"""创建异步数据库连接。Worker 每次任务使用独立事件循环，因此不复用上一轮循环中的数据库连接。"""

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from backend.core.config import get_settings

# Celery solo 任务会多次通过 asyncio.run 创建事件循环。NullPool 不保留连接，避免跨循环复用连接导致错误。
engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
# expire_on_commit=False 让提交后仍可读取已加载的属性，例如 task.id，避免序列化响应时再次查询数据库。
Session = async_sessionmaker(engine, expire_on_commit=False)


async def get_session():
    """为接口请求提供独立的数据库会话。请求结束时关闭会话，尚未提交的修改会被回滚。"""
    async with Session() as session:
        yield session
