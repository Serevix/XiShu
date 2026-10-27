"""通过实际 PostgreSQL、行级权限、LangGraph checkpoint 和 Docker 沙箱验证完整项目流程。"""
import asyncio
import io
import json
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from backend.main import app
from backend.core.database import Session
from backend.core.config import get_settings
from backend.core.storage import read_table
from backend.models import User, Dataset, Order
from backend.services.datasets import import_dataset
from backend.workers.tasks import analyze
from backend.execution.sql_executor import execute_query
from scripts.generate_demo_data import generate_tables

pytestmark = pytest.mark.integration


@pytest.fixture
def workspace(monkeypatch):
    """创建独立测试用户并禁止向实际队列发任务，由测试直接调用 Worker 的执行逻辑。"""
    def no_dispatch(*args):
        """不发送真实队列消息，测试自行执行任务，避免后台 Worker 与测试同时处理同一任务。"""
        return True
    monkeypatch.setattr('backend.api.routes.datasets.dispatch', no_dispatch)
    monkeypatch.setattr('backend.api.routes.analysis_tasks.dispatch', no_dispatch)
    monkeypatch.setattr('backend.memory.service.enqueue', no_dispatch)
    monkeypatch.setattr(get_settings(), 'memory_enabled', False)
    identifiers = []
    with TestClient(app) as client:
        headers = []
        for index in range(2):
            response = client.post('/api/auth/register', json={'username':'test_' + uuid4().hex[:12], 'password':'local-integration-only'})
            assert response.status_code == 201, response.text
            token = response.json()['token']
            header = {'Authorization':'Bearer ' + token}
            identifiers.append(client.get('/api/auth/me', headers=header).json()['id'])
            headers.append(header)
        yield client, headers
    asyncio.run(cleanup_users(identifiers))


async def cleanup_users(identifiers):
    """只删除本次测试用户及其关联数据，不清理其他用户或项目资源。"""
    async with Session.begin() as session:
        await session.execute(delete(User).where(User.id.in_(identifiers)))


def ready_demo(client, headers):
    """通过 API 创建模拟数据，再调用实际导入逻辑，使测试数据集就绪。"""
    response = client.post('/api/datasets/demo', headers=headers)
    assert response.status_code == 201, response.text
    identifier = response.json()['id']
    asyncio.run(import_dataset(identifier))
    assert client.get('/api/datasets/' + identifier, headers=headers).json()['status'] == 'ready'
    return identifier


def test_complete_pipeline_and_isolation(workspace):
    """检查 SQL、绘图、沙箱、报告、事件流和用户隔离能否在完整流程中正常工作。"""
    client, headers = workspace
    dataset = ready_demo(client, headers[0])
    foreign = ready_demo(client, headers[1])
    task = client.post('/api/analysis-tasks', headers=headers[0], json={'dataset_id':dataset,'question':'比较2017-10与2017-11的品类变化并绘图','mode':'demo'}).json()
    asyncio.run(analyze(task['id']))
    result = client.get('/api/analysis-tasks/' + task['id'], headers=headers[0]).json()
    assert result['status'] == 'succeeded', result
    tables = [item for item in result['artifacts'] if item.get('sql')]
    summary = read_table(tables[0]['id'], task['id'])['rows'][0]
    assert summary['order_count'] == 66
    assert Decimal(summary['item_amount']) == Decimal('6550.00')
    changes = read_table(tables[1]['id'], task['id'])['rows']
    assert changes[0]['dimension'] == 'electronics'
    assert Decimal(changes[0]['item_amount']) == Decimal('-990.00')
    assert any(item.get('python') for item in result['artifacts'])
    report = next(item for item in result['artifacts'] if item['kind'] == 'report')
    response = client.get('/api/artifacts/' + report['id'], headers=headers[0])
    assert response.status_code == 200 and '6550.00' in response.text
    stream = client.get('/api/analysis-tasks/' + task['id'] + '/events', headers=headers[0])
    assert 'report_ready' in stream.text
    assert client.get('/api/datasets/' + dataset, headers=headers[1]).status_code == 404
    assert client.get('/api/analysis-tasks/' + task['id'], headers=headers[1]).status_code == 404
    assert client.get('/api/artifacts/' + report['id'], headers=headers[1]).status_code == 404
    assert client.post('/api/analysis-tasks', headers=headers[1], json={'dataset_id':dataset,'question':'统计订单'}).status_code == 404
    # 即使 SQL 明确指定其他数据集，数据库行级权限也应过滤其数据，不能只依赖模型填写 WHERE。
    isolated = asyncio.run(execute_query(task['id'], dataset, f"SELECT COUNT(*) AS n FROM orders WHERE dataset_id='{foreign}'"))
    assert isolated['preview'][0]['n'] == 0


def test_clarification_checkpoint_resume(workspace):
    """检查澄清后 Worker 能否结束本次执行，并在收到答案后从原 checkpoint 继续。"""
    client, headers = workspace
    dataset = ready_demo(client, headers[0])
    task = client.post('/api/analysis-tasks', headers=headers[0], json={'dataset_id':dataset,'question':'先确认口径，再分析品类金额','mode':'demo'}).json()
    asyncio.run(analyze(task['id']))
    result = client.get('/api/analysis-tasks/' + task['id'], headers=headers[0]).json()
    assert result['status'] == 'waiting_input'
    assert client.post('/api/analysis-tasks/' + task['id'] + '/resume', headers=headers[0], json={'answer':'确认使用已送达订单、不含运费'}).status_code == 200
    asyncio.run(analyze(task['id']))
    result = client.get('/api/analysis-tasks/' + task['id'], headers=headers[0]).json()
    assert result['status'] == 'succeeded', result


def test_cancel_queued_task(workspace):
    """检查排队任务被取消后，即使任务消息稍后才到达 Worker，也不会重新执行。"""
    client, headers = workspace
    dataset = ready_demo(client, headers[0])
    task = client.post('/api/analysis-tasks', headers=headers[0], json={'dataset_id':dataset,'question':'统计订单','mode':'demo'}).json()
    assert client.post('/api/analysis-tasks/' + task['id'] + '/cancel', headers=headers[0]).status_code == 200
    asyncio.run(analyze(task['id']))
    result = client.get('/api/analysis-tasks/' + task['id'], headers=headers[0]).json()
    assert result['status'] == 'cancelled' and not result['artifacts']


def test_duplicate_import_rolls_back(workspace):
    """检查重复主键是否导致整个导入回滚，四张表都不能留下部分数据。"""
    client, headers = workspace
    tables = generate_tables()
    tables['order_items'].append(dict(tables['order_items'][0]))
    files = []
    for table, rows in tables.items():
        content = pd.DataFrame(rows).to_csv(index=False).encode('utf-8-sig')
        files.append(('files', (table + '.csv', content, 'text/csv')))
    response = client.post('/api/datasets', headers=headers[0], data={'name':'重复键测试'}, files=files)
    assert response.status_code == 201, response.text
    dataset = response.json()['id']
    preview = client.get('/api/datasets/' + dataset + '/preview', headers=headers[0]).json()
    mapping = {table:body['mapping'] for table, body in preview.items()}
    assert client.post('/api/datasets/' + dataset + '/import', headers=headers[0], json={'mapping':mapping}).status_code == 200
    with pytest.raises(ValueError, match='主键重复'):
        asyncio.run(import_dataset(dataset))
    assert client.get('/api/datasets/' + dataset, headers=headers[0]).json()['status'] == 'failed'
    result = asyncio.run(execute_query(uuid4().hex, dataset, 'SELECT COUNT(*) AS n FROM orders'))
    assert result['preview'][0]['n'] == 0


def test_excel_import(workspace):
    """生成真实的四工作表 XLSX 文件，检查预览、字段映射和金额导入。"""
    client, headers = workspace
    content = io.BytesIO()
    with pd.ExcelWriter(content, engine='openpyxl') as writer:
        for table, rows in generate_tables().items():
            pd.DataFrame(rows).to_excel(writer, sheet_name=table, index=False)
    response = client.post('/api/datasets', headers=headers[0], data={'name':'Excel测试'},
                           files=[('files', ('business.xlsx', content.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'))])
    assert response.status_code == 201, response.text
    dataset = response.json()['id']
    assert client.post('/api/datasets/' + dataset + '/import', headers=headers[0], json={}).status_code == 200
    asyncio.run(import_dataset(dataset))
    assert client.get('/api/datasets/' + dataset, headers=headers[0]).json()['status'] == 'ready'


def test_reader_database_permissions():
    """绕过应用 SQL 校验直接使用只读账号连接数据库，检查数据库权限是否仍禁止写入。"""
    async def check():
        """用实际只读连接检查未设置数据集时看不到业务行，并确认 UPDATE 被拒绝。"""
        engine = create_async_engine(get_settings().sql_reader_url, poolclass=NullPool)
        try:
            async with engine.begin() as connection:
                assert await connection.scalar(text('SELECT COUNT(*) FROM orders')) == 0
            with pytest.raises(Exception, match='permission denied'):
                async with engine.begin() as connection:
                    await connection.execute(text("UPDATE orders SET status='tampered' WHERE false"))
        finally:
            await engine.dispose()
    asyncio.run(check())
