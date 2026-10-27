"""提供数据集管理接口，每次访问都检查数据集是否属于当前用户。"""
import asyncio
from pathlib import Path
from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException, Query
from sqlalchemy import select
from backend.core.config import get_settings
from backend.core.database import get_session
from backend.api.dependencies import current_user
from backend.models.dataset import Dataset
from backend.models.base import new_id
from backend.repositories.datasets import owned_dataset
from backend.schemas.datasets import ImportRequest
from backend.services.datasets import save_uploads, preview_dataset, serialize_dataset
from backend.services.analysis_tasks import dispatch
from backend.ingestion.mapping import DEFAULT_METRICS
from scripts.generate_demo_data import write_demo_files

router = APIRouter(prefix='/api/datasets', tags=['数据集'])


@router.get('')
async def list_datasets(user=Depends(current_user), session=Depends(get_session)):
    """按创建时间从新到旧列出当前用户的数据集。"""
    rows = await session.scalars(select(Dataset).where(Dataset.user_id == user.id).order_by(Dataset.created_at.desc()))
    return [serialize_dataset(row) for row in rows]


@router.post('', status_code=201)
async def upload_dataset(name: str = Form(..., min_length=1, max_length=120), files: list[UploadFile] = File(...),
                         user=Depends(current_user), session=Depends(get_session)):
    """保存上传的 CSV 或 XLSX 文件，等待用户预览并确认字段映射后再导入。"""
    if len(files) > 4:
        raise HTTPException(422, '最多上传四个文件')
    dataset = await save_uploads(session, user.id, name, files)
    return serialize_dataset(dataset)


@router.post('/demo', status_code=201)
async def create_demo(user=Depends(current_user), session=Depends(get_session)):
    """生成使用固定随机种子的四张模拟表。这里不下载真实 Olist 数据。"""
    key = new_id()
    manifest = await asyncio.to_thread(write_demo_files, get_settings().storage / 'uploads' / key)
    dataset = Dataset(id=key, user_id=user.id, name='模拟电商数据 · 2017年10—11月', source_type='synthetic',
                       status='importing', file_manifest=manifest, metric_definitions=dict(DEFAULT_METRICS))
    session.add(dataset)
    await session.commit()
    dispatched = dispatch('analytics.import', key)
    return {**serialize_dataset(dataset), 'dispatched': dispatched}


@router.get('/{dataset_id}')
async def get_dataset(dataset_id: str, user=Depends(current_user), session=Depends(get_session)):
    """返回数据集的导入状态、错误信息和行数，供前端定期检查。"""
    return serialize_dataset(await owned_dataset(session, dataset_id, user.id))


@router.get('/{dataset_id}/preview')
async def preview(dataset_id: str, encoding: str = Query('utf-8-sig', pattern='^(utf-8-sig|gb18030)$'),
                  user=Depends(current_user), session=Depends(get_session)):
    """读取少量数据和建议字段映射供用户预览。解析失败时返回具体错误。"""
    dataset = await owned_dataset(session, dataset_id, user.id)
    try:
        return await preview_dataset(dataset, encoding)
    except (ValueError, UnicodeError) as error:
        raise HTTPException(422, str(error)[:500]) from None


@router.post('/{dataset_id}/import')
async def confirm_import(dataset_id: str, payload: ImportRequest, user=Depends(current_user), session=Depends(get_session)):
    """锁住尚未就绪的数据集，保存用户确认的字段映射和统计口径，再发送导入任务。"""
    dataset = await owned_dataset(session, dataset_id, user.id)
    await session.refresh(dataset, with_for_update=True)
    if dataset.status not in ('uploaded', 'failed'):
        raise HTTPException(409, '该数据集已导入或正在导入')
    manifest = dict(dataset.file_manifest)
    for table, sheet in payload.sheets.items():
        if table not in manifest or Path(manifest[table]['path']).suffix != '.xlsx':
            raise HTTPException(422, '工作表选择无效')
        manifest[table] = {**manifest[table], 'sheet': sheet}
    dataset.file_manifest = manifest
    dataset.field_mapping = {'columns': payload.mapping, 'encoding': payload.encoding}
    dataset.metric_definitions = {**DEFAULT_METRICS, 'currency': payload.currency, 'valid_status': payload.valid_status}
    dataset.status, dataset.error_info = 'importing', None
    await session.commit()
    return {'status': dataset.status, 'dispatched': dispatch('analytics.import', dataset.id)}


@router.delete('/{dataset_id}')
async def delete_dataset(dataset_id: str, user=Depends(current_user), session=Depends(get_session)):
    """只允许删除没有关联分析任务且不在导入的数据集，避免影响正在执行或已有结果的任务。"""
    from backend.models.analysis_task import AnalysisTask
    dataset = await owned_dataset(session, dataset_id, user.id)
    active = await session.scalar(select(AnalysisTask.id).where(AnalysisTask.dataset_id == dataset.id).limit(1))
    if dataset.status == 'importing' or active:
        raise HTTPException(409, '正在导入或已被分析任务引用的数据集不能删除')
    await session.delete(dataset)
    await session.commit()
    return {'deleted': True}
