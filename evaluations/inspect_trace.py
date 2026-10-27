"""读取指定评测任务的 checkpoint，查看报告被拒绝及模型修正的原因。"""
import argparse
import asyncio
import json
from pathlib import Path
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from backend.core.config import get_settings
from backend.core.database import Session
from backend.models import AnalysisTask
from scripts.init_db import postgres_dsn


async def inspect(directory: Path):
    """提取本轮任务的 finish 参数，不输出密钥、数据库地址或完整模型上下文。"""
    records = json.loads((directory / 'results.json').read_text(encoding='utf-8'))
    extracted = {}
    async with AsyncPostgresSaver.from_conn_string(postgres_dsn(get_settings().database_url)) as saver:
        for record in records['cases']:
            async with Session() as session:
                task = await session.get(AnalysisTask, record['task_id'])
                thread = task.thread_id
            calls = {}
            async for checkpoint in saver.alist({'configurable': {'thread_id': thread}}):
                action = checkpoint.checkpoint.get('channel_values', {}).get('action', {})
                if action.get('name') == 'finish':
                    key = action.get('call_id', 'demo')
                    if key not in calls:
                        calls[key] = {'call_id': key, 'arguments': action['arguments'], 'verified': action.get('verified')}
            extracted[record['id']] = list(reversed(list(calls.values())))
    (directory / 'finish-attempts.json').write_text(json.dumps(extracted, ensure_ascii=False, indent=2), encoding='utf-8')
    print('已保存各题报告参数与核对结果；不包含凭据。')


def main():
    """读取指定评测目录中任务的报告参数，帮助检查模型为何重试。"""
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    asyncio.run(inspect(args.directory))


if __name__ == '__main__':
    main()
