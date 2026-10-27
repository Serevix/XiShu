"""检查应用依赖是否可用，并返回运行模式。响应不包含连接地址或密钥。"""
from fastapi import APIRouter
from sqlalchemy import text
from redis.asyncio import Redis
from backend.core.config import get_settings
from backend.core.database import engine
from backend.memory.vectors import vector_health

router = APIRouter(prefix='/api', tags=['健康'])


@router.get('/health')
async def health():
    """检查数据库、Redis 和记忆向量库，返回各项是否可用，以及模型是否已配置。"""
    settings = get_settings()
    result = {'application': True, 'database': False, 'redis': False,
              'model_ready': bool(settings.model_api_key), 'model_name': settings.model_name,
              'default_mode': settings.model_mode, 'model_provider': settings.model_provider,
              'demo_notice': '演示模式使用规则，不调用聊天模型', 'memory_enabled':settings.memory_enabled,
              'memory_database':settings.milvus_database, 'milvus':False}
    try:
        async with engine.connect() as connection:
            result['database'] = await connection.scalar(text('SELECT 1')) == 1
    except Exception:
        pass
    redis = Redis.from_url(settings.redis_url, socket_connect_timeout=2)
    try:
        result['redis'] = await redis.ping()
    except Exception:
        pass
    finally:
        await redis.aclose()
    result['milvus'] = await vector_health()
    return result
