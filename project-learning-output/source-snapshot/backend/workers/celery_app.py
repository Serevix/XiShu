"""配置 Celery 使用 Redis 传递任务消息。在 Windows 中使用 solo 模式执行任务。"""
from celery import Celery
from backend.core.config import get_settings

# broker 指消息中介。Redis 保存待处理消息，Worker 读取消息后才执行分析代码。
# 这里不使用 Celery 的 result_backend 保存返回值。任务状态、事件和文件信息由本项目保存到 PostgreSQL。
celery_app = Celery('agent_analytics', broker=get_settings().redis_url, include=['backend.workers.tasks'])
celery_app.conf.update(
    task_serializer='json', accept_content=['json'], task_ignore_result=True,
    # 记忆同步使用独立队列和 Worker，避免慢嵌入请求占用分析任务的执行进程。
    task_routes={'analytics.memory_sync': {'queue':'analytics-memory'}},
    # 任务执行完再确认消息，进程失联时可能重新发送。因此业务代码仍需加锁，并检查是否已经处理过。
    task_acks_late=True, task_reject_on_worker_lost=True, worker_prefetch_multiplier=1,
    # visibility_timeout 是未确认消息重新发送前的等待时间，不是任务允许执行多久。
    broker_transport_options={'visibility_timeout': 600}, broker_connection_retry_on_startup=True,
    beat_schedule={'recover-every-minute': {'task': 'analytics.recover', 'schedule': 60.0}},
    timezone='Asia/Shanghai', enable_utc=True,
)
