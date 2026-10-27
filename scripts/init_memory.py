"""添加 PostgreSQL 记忆与会话表，并初始化项目专用 Milvus 数据库，不删除原有数据。"""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateTable, CreateIndex
from backend.models import Base
from backend.core.config import ROOT, get_settings
from backend.core.database import engine
from backend.memory.vectors import initialize_vector_store


async def main():
    """创建四张新增表并导出建表 SQL，然后创建长期记忆所用的 Milvus 集合。"""
    if make_url(get_settings().database_url).database != 'agent_analytics':
        raise ValueError('新增表只允许创建在 agent_analytics')
    names = {'user_memories','user_question_patterns','analysis_conversations','conversation_messages'}
    tables = [table for table in Base.metadata.sorted_tables if table.name in names]
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=tables)
    statements = [str(CreateTable(table).compile(dialect=engine.dialect)) + ';' for table in tables]
    statements += [str(CreateIndex(index).compile(dialect=engine.dialect)) + ';' for table in tables for index in table.indexes]
    (ROOT / 'migrations/002_memory_context.sql').write_text('-- 增量记忆与会话表；运行 scripts/init_memory.py 幂等初始化。\n' + '\n\n'.join(statements), encoding='utf-8')
    info = await asyncio.to_thread(initialize_vector_store)
    await engine.dispose()
    print('PostgreSQL 新增四表；Milvus：' + str(info))


if __name__ == '__main__':
    asyncio.run(main())
