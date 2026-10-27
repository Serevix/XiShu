"""在本项目专用的 Milvus 数据库中管理记忆向量，每次检索都限定当前用户。"""
import asyncio
import json
import math
from pymilvus import MilvusClient, DataType
import httpx
from backend.core.config import get_settings


def connect(db_name: str | None = None) -> MilvusClient:
    """创建带超时设置的 Milvus 客户端，并明确指定数据库，不修改其他客户端的默认数据库。"""
    settings = get_settings()
    if db_name is None and settings.milvus_database != 'agent_analytics_memory':
        raise ValueError('长期记忆只允许访问 agent_analytics_memory')
    return MilvusClient(uri=settings.milvus_uri, token=settings.milvus_token,
                        db_name=db_name or settings.milvus_database, timeout=settings.milvus_timeout_seconds)


def initialize_vector_store() -> dict:
    """创建本项目的数据库和集合。已存在时复用；已有集合的向量维度不兼容时，报错而不删除重建。"""
    settings = get_settings()
    if settings.milvus_database != 'agent_analytics_memory':
        raise ValueError('初始化仅允许 agent_analytics_memory，避免修改其他项目数据库')
    admin = connect('default')
    try:
        before = admin.list_databases()
        if settings.milvus_database not in before:
            admin.create_database(settings.milvus_database)
    finally:
        admin.close()
    client = connect()
    try:
        if not client.has_collection(settings.milvus_collection):
            schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field('id', DataType.VARCHAR, is_primary=True, max_length=36)
            schema.add_field('user_id', DataType.VARCHAR, max_length=36)
            schema.add_field('kind', DataType.VARCHAR, max_length=16)
            schema.add_field('text', DataType.VARCHAR, max_length=8192)
            schema.add_field('version', DataType.INT64)
            # 向量列的维度必须与嵌入模型一致。已有集合维度不符时，不能直接写入，因此后面要检查已有定义。
            schema.add_field('vector', DataType.FLOAT_VECTOR, dim=settings.embedding_dimensions)
            indexes = client.prepare_index_params()
            indexes.add_index(field_name='vector', index_type='HNSW', metric_type='COSINE', params={'M':16, 'efConstruction':128})
            indexes.add_index(field_name='user_id', index_type='INVERTED')
            client.create_collection(settings.milvus_collection, schema=schema, index_params=indexes, consistency_level='Strong')
        info = client.describe_collection(settings.milvus_collection)
        dimension = next(field['params']['dim'] for field in info['fields'] if field['name'] == 'vector')
        if int(dimension) != settings.embedding_dimensions:
            raise ValueError('已有记忆向量维度不同，请核对嵌入模型配置；未修改原集合')
        client.load_collection(settings.milvus_collection)
        return {'database': settings.milvus_database, 'collection': settings.milvus_collection, 'dimensions': int(dimension)}
    finally:
        client.close()


async def embed(text: str) -> list[float]:
    """调用嵌入接口将文本转成向量，检查向量维度和数值是否合法。密钥只从配置中读取。"""
    settings = get_settings()
    key = settings.embedding_api_key or settings.model_api_key
    if not key:
        raise ValueError('尚未配置嵌入模型密钥')
    async with httpx.AsyncClient(timeout=settings.embedding_timeout_seconds) as client:
        response = await client.post(settings.embedding_base_url.rstrip('/') + '/embeddings',
                                     headers={'Authorization': 'Bearer ' + key},
                                     json={'model': settings.embedding_model, 'input': text, 'encoding_format': 'float'})
        if response.is_error:
            raise ValueError(f'嵌入接口返回 {response.status_code}')
        vector = response.json()['data'][0]['embedding']
    if len(vector) != settings.embedding_dimensions or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in vector):
        raise ValueError('嵌入向量维度或数值无效')
    return [float(value) for value in vector]


def write_vector(record: dict):
    """以记忆 ID 写入或更新对应向量，不修改其他记录或其他数据库。"""
    client = connect()
    try:
        client.upsert(get_settings().milvus_collection, [record])
    finally:
        client.close()


def delete_vector(identifier: str, user_id: str):
    """只删除记忆 ID 和用户 ID 同时匹配的向量，不删除集合或数据库。"""
    client = connect()
    try:
        client.delete(get_settings().milvus_collection, filter='id == ' + json.dumps(identifier) + ' and user_id == ' + json.dumps(user_id))
    finally:
        client.close()


def search_vectors(user_id: str, vector: list[float]) -> list[dict]:
    """检索当前用户的常问模板向量。返回候选后，调用方还需检查 PostgreSQL 中的版本和删除状态。"""
    settings = get_settings()
    client = connect()
    try:
        # 在 Milvus 检索时先限制用户，service 随后再检查数据库记录，避免使用已删除或过期的候选。
        result = client.search(settings.milvus_collection, data=[vector],
                               filter='user_id == ' + json.dumps(user_id) + ' and kind == "pattern"',
                               limit=settings.memory_top_k, output_fields=['id', 'user_id', 'kind', 'text', 'version'],
                               search_params={'metric_type':'COSINE','params':{'ef':64}}, consistency_level='Strong')
        return result[0]
    finally:
        client.close()


async def vector_health() -> bool:
    """连接 Milvus 并检查项目记忆集合是否存在，连接或检查失败时返回 False。"""
    def check():
        """在线程中调用同步 Milvus SDK，避免阻塞 API 使用的异步事件循环。"""
        client = connect()
        try:
            return client.has_collection(get_settings().milvus_collection)
        finally:
            client.close()
    try:
        return await asyncio.to_thread(check)
    except Exception:
        return False
