"""读取环境配置，并避免将连接凭据和密钥写入接口响应、事件或模型消息。"""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """定义配置项，并从项目根目录的 .env 读取对应值。"""

    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore')
    # 1. PostgreSQL：业务写入和模型查询分别使用不同的数据库连接配置。
    database_url: str = Field(repr=False)
    sql_reader_url: str = Field(repr=False)

    # 2. 登录认证：JWT 密钥用于签发登录令牌，不是调用模型接口的密钥。
    jwt_secret: str = Field(min_length=32, repr=False)

    # 3. 后台队列：配置 Celery 使用的 Redis 地址。
    redis_url: str = 'redis://127.0.0.1:6380/0'

    # 4. 模型连接：配置服务商、接口地址、模型名称和 API 密钥。
    model_provider: Literal['compatible', 'siliconflow'] = 'compatible'
    model_mode: Literal['demo', 'llm'] = 'demo'
    model_base_url: str = 'https://api.deepseek.com/v1'
    model_name: str = 'deepseek-chat'
    model_api_key: str = Field(default='', repr=False)

    # 5. 模型生成：配置输出长度、采样参数和本项目采用的上下文长度上限。
    model_context_tokens: int = Field(default=32000, ge=4096)
    model_max_tokens: int = Field(default=1800, ge=128, le=32768)
    model_temperature: float = Field(default=0.1, ge=0, le=2)
    model_enable_thinking: bool = False
    model_timeout_seconds: int = Field(default=60, ge=1)

    # 6～7. 文件与容器：配置结果存储目录和执行 Python 所用的沙箱镜像。
    storage_root: Path = ROOT / 'storage'
    sandbox_image: str = 'agent-analytics-sandbox:local'

    # 8. 分析流程：限制决策步骤数和任务执行时间，防止一直运行。
    max_agent_steps: int = Field(default=18, ge=1, le=100)
    task_timeout_seconds: int = Field(default=300, ge=1)
    tool_concurrency: int = Field(default=3, ge=1, le=8)
    max_tools_per_round: int = Field(default=8, ge=1, le=16)

    # 9～10. 上传与查询：限制上传大小、SQL 耗时和返回行数。
    max_upload_mb: int = Field(default=40, ge=1)
    max_sql_rows: int = Field(default=1000, ge=1, le=10000)
    sql_timeout_ms: int = Field(default=10000, ge=1)

    # 11～12. 长期记忆：配置专用 Milvus 数据库和将文本转成向量的嵌入模型。
    memory_enabled: bool = True
    milvus_uri: str = 'http://127.0.0.1:19530'
    milvus_token: str = Field(default='', repr=False)
    milvus_database: str = 'agent_analytics_memory'
    milvus_collection: str = 'user_memories'
    milvus_timeout_seconds: int = Field(default=5, ge=1, le=60)
    embedding_base_url: str = 'https://api.siliconflow.cn/v1'
    embedding_api_key: str = Field(default='', repr=False)
    embedding_model: str = 'BAAI/bge-m3'
    embedding_dimensions: int = Field(default=1024, ge=64, le=8192)
    embedding_timeout_seconds: int = Field(default=15, ge=1, le=120)
    memory_top_k: int = Field(default=5, ge=1, le=20)
    memory_min_similarity: float = Field(default=.4, ge=-1, le=1)
    memory_pattern_min_uses: int = Field(default=2, ge=1, le=100)

    # 13. 历史摘要：配置近期消息保留量和摘要长度，原文与摘要进度分别保存。
    context_recent_messages: int = Field(default=6, ge=2, le=30)
    context_summary_trigger: float = Field(default=.65, ge=.3, le=.9)
    context_summary_max_tokens: int = Field(default=700, ge=128, le=2000)

    @property
    def storage(self) -> Path:
        """将配置的存储目录转换为绝对路径。相对路径以项目根目录为起点。"""
        path = self.storage_root
        return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


@lru_cache
def get_settings() -> Settings:
    """读取并缓存配置，让后续请求复用同一个配置对象，不反复读取 .env。"""
    return Settings()
