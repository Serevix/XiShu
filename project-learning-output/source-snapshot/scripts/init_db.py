"""初始化本项目的数据库表、只读账号和行级权限，不使用项目一数据库。"""
import asyncio
import sys
from pathlib import Path
import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateTable, CreateIndex
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core.config import ROOT, get_settings
from backend.core.database import engine
from backend.models import Base


def postgres_dsn(url: str) -> str:
    """将 SQLAlchemy 的连接 URL 转换为 psycopg 能使用的 PostgreSQL 连接字符串。"""
    return make_url(url).set(drivername='postgresql').render_as_string(hide_password=False)


async def initialize_database() -> None:
    """创建尚不存在的表、只读账号、行级权限和 checkpoint 表，已有表不会删除重建。"""
    settings = get_settings()
    if make_url(settings.database_url).database != 'agent_analytics':
        raise RuntimeError('初始化只允许作用于 agent_analytics 数据库')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    reader = make_url(settings.sql_reader_url)
    if reader.database != 'agent_analytics' or reader.username != 'agent_analytics_reader':
        raise RuntimeError('只读角色配置不符合项目二约定')
    async with await psycopg.AsyncConnection.connect(postgres_dsn(settings.database_url), autocommit=True) as connection:
        async with connection.cursor() as cursor:
            await cursor.execute('SELECT 1 FROM pg_roles WHERE rolname = %s', (reader.username,))
            if await cursor.fetchone() is None:
                await cursor.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS')
                                     .format(sql.Identifier(reader.username), sql.Literal(reader.password)))
            await cursor.execute(sql.SQL('GRANT CONNECT ON DATABASE agent_analytics TO {}').format(sql.Identifier(reader.username)))
            await cursor.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(reader.username)))
            for table in ('customers', 'products', 'orders', 'order_items'):
                await cursor.execute(sql.SQL('GRANT SELECT ON {} TO {}').format(sql.Identifier(table), sql.Identifier(reader.username)))
                await cursor.execute(sql.SQL('ALTER TABLE {} ENABLE ROW LEVEL SECURITY').format(sql.Identifier(table)))
                await cursor.execute('SELECT 1 FROM pg_policies WHERE schemaname=%s AND tablename=%s AND policyname=%s',
                                     ('public', table, 'dataset_scope'))
                if await cursor.fetchone() is None:
                    await cursor.execute(sql.SQL("CREATE POLICY dataset_scope ON {} FOR SELECT TO {} USING (dataset_id = current_setting('app.dataset_id', true))")
                                         .format(sql.Identifier(table), sql.Identifier(reader.username)))
            await cursor.execute('SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=%s', (reader.username,))
            if any(await cursor.fetchone()):
                raise RuntimeError('分析查询角色不得具有超级用户或绕过行级策略权限')
    async with AsyncPostgresSaver.from_conn_string(postgres_dsn(settings.database_url)) as saver:
        await saver.setup()
    # 根据实际模型导出建表 SQL，便于在 Navicat 阅读，不把只读账号密码写入迁移文件。
    dialect = engine.dialect
    statements = [str(CreateTable(table).compile(dialect=dialect)).strip() + ';' for table in Base.metadata.sorted_tables]
    statements += [str(CreateIndex(index).compile(dialect=dialect)) + ';'
                   for table in Base.metadata.sorted_tables for index in table.indexes]
    (ROOT / 'migrations' / '001_initial.sql').write_text(
        '-- 由 scripts/init_db.py 导出；首次初始化请运行该脚本以同时配置 RLS 和 checkpoint。\n' + '\n\n'.join(statements), encoding='utf-8')
    await engine.dispose()
    print(f'agent_analytics：{len(Base.metadata.tables)} 张应用表、只读角色、RLS 与 checkpoint 初始化完成。')


if __name__ == '__main__':
    asyncio.run(initialize_database())
