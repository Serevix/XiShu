"""保存和预览上传文件，并由独立 Worker 校验、导入四张业务表。"""
import asyncio
import hashlib
from pathlib import Path
from sqlalchemy import select, text
from fastapi import HTTPException
from backend.core.config import get_settings
from backend.core.database import Session, engine
from backend.models.dataset import Dataset
from backend.models.base import new_id
from backend.ingestion.mapping import FIELDS, DEFAULT_METRICS, infer_table, default_mapping
from backend.ingestion.parsers import read_frame, workbook_sheets
from backend.ingestion.validation import normalize_table, validate_relations
from backend.repositories.business_data import insert_tables


def serialize_dataset(dataset: Dataset) -> dict:
    """整理数据集的公开信息，不返回服务器上的上传文件路径。"""
    return {'id': dataset.id, 'name': dataset.name, 'status': dataset.status,
            'source_type': dataset.source_type, 'data_version': dataset.data_version,
            'metric_definitions': dataset.metric_definitions, 'summary': dataset.import_summary,
            'error': dataset.error_info, 'created_at': dataset.created_at.isoformat()}


async def save_uploads(session, user_id: str, name: str, files) -> Dataset:
    """在大小限制内保存四个 CSV 或一个 XLSX。文件类型不符、业务表重复或缺失时，返回错误。"""
    identifier = new_id()
    folder = get_settings().storage / 'uploads' / identifier
    folder.mkdir(parents=True)
    manifest, total = {}, 0
    try:
        for upload in files:
            extension = Path(upload.filename or '').suffix.lower()
            if extension not in ('.csv', '.xlsx'):
                raise ValueError('只接受 CSV 或 XLSX 文件')
            # 用服务端生成的 UUID 作为实际文件名，原名只用于展示，防止路径穿越和同名文件覆盖。
            path = folder / f'{new_id()}{extension}'
            digest = hashlib.sha256()
            with path.open('wb') as handle:
                while chunk := await upload.read(1024 * 1024):
                    total += len(chunk)
                    if total > get_settings().max_upload_mb * 1024 * 1024:
                        raise ValueError('上传总大小超过限制')
                    digest.update(chunk)
                    handle.write(chunk)
            if extension == '.xlsx':
                for sheet in await asyncio.to_thread(workbook_sheets, path):
                    table = infer_table(sheet)
                    if table:
                        if table in manifest:
                            raise ValueError(f'{table} 被重复提供')
                        manifest[table] = {'path': str(path), 'name': upload.filename, 'sheet': sheet, 'sha256': digest.hexdigest()}
            else:
                table = infer_table(upload.filename or '')
                if table is None or table in manifest:
                    raise ValueError('CSV 文件名应为 customers/products/orders/order_items，或官方 Olist 名称')
                manifest[table] = {'path': str(path), 'name': upload.filename, 'sheet': None, 'sha256': digest.hexdigest()}
        if set(manifest) != set(FIELDS):
            raise ValueError('请一次提供客户、商品、订单和商品明细四张表')
        dataset = Dataset(id=identifier, user_id=user_id, name=name, file_manifest=manifest,
                          metric_definitions=dict(DEFAULT_METRICS))
        session.add(dataset)
        await session.commit()
        return dataset
    except ValueError as error:
        # 仅清理本次上传生成的目录，路径由服务端 UUID 确定，不使用用户提供的删除路径。
        for file in folder.iterdir():
            file.unlink()
        folder.rmdir()
        raise HTTPException(422, str(error)) from None


async def preview_dataset(dataset: Dataset, encoding: str = 'utf-8-sig') -> dict:
    """读取每张表的列名和少量样例，给出建议映射，供用户确认后导入。"""
    result = {}
    for table, entry in dataset.file_manifest.items():
        frame = await asyncio.to_thread(read_frame, Path(entry['path']), entry.get('sheet'), encoding, 5)
        result[table] = {'columns': list(frame.columns), 'rows': frame.to_dict('records'),
                         'mapping': default_mapping(list(frame.columns), table), 'fields': FIELDS[table]}
    return result


async def import_dataset(dataset_id: str) -> None:
    """锁住本次数据集并校验全部内容。四表数据和导入成功状态在同一事务中保存。"""
    async with engine.connect() as lock_connection:
        locked = await lock_connection.scalar(text('SELECT pg_try_advisory_lock(hashtextextended(:key, 0))'), {'key': 'import:' + dataset_id})
        await lock_connection.commit()
        if not locked:
            return
        try:
            async with Session() as session:
                dataset = await session.get(Dataset, dataset_id)
                if dataset is None or dataset.status == 'ready':
                    return
                manifest, mapping = dataset.file_manifest, dataset.field_mapping
            tables = {}
            for table, entry in manifest.items():
                frame = await asyncio.to_thread(read_frame, Path(entry['path']), entry.get('sheet'), mapping.get('encoding', 'utf-8-sig'))
                columns = mapping.get('columns', {}).get(table) or default_mapping(list(frame.columns), table)
                tables[table] = await asyncio.to_thread(normalize_table, frame, table, columns, dataset_id)
            # 先检查字段类型、主键和表间关联，再写数据库。文件上传完成并不表示已经可以分析。
            validate_relations(tables)
            # 四表所有批次和 ready 状态一起提交。任何插入失败都会回滚，避免留下只导入一部分的数据。
            async with Session.begin() as session:
                await insert_tables(session, tables)
                # 导入后立即执行 ANALYZE 更新数据分布统计，帮助 PostgreSQL 为首条分析查询选择合适的执行计划。
                await session.execute(text('ANALYZE public.customers, public.products, public.orders, public.order_items'))
                dataset = await session.get(Dataset, dataset_id)
                dataset.status = 'ready'
                dataset.error_info = None
                dataset.import_summary = {table: len(rows) for table, rows in tables.items()}
        except Exception as error:
            # 导入失败后，另开事务保存失败状态和原因，让页面显示错误，用户也能据此修正并重试。
            async with Session.begin() as session:
                dataset = await session.get(Dataset, dataset_id)
                if dataset:
                    dataset.status = 'failed'
                    dataset.error_info = str(error)[:1000] if isinstance(error, ValueError) else '导入失败，请查看 Worker 日志并重试'
            raise
        finally:
            await lock_connection.execute(text('SELECT pg_advisory_unlock(hashtextextended(:key, 0))'), {'key': 'import:' + dataset_id})
            await lock_connection.commit()
