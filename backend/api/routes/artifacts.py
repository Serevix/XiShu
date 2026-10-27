"""提供结果文件下载接口。文件路径由服务器根据文件 ID 查找。"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from backend.api.dependencies import current_user
from backend.core.database import get_session
from backend.core.storage import read_artifact
from backend.repositories.analysis_tasks import owned_task

router = APIRouter(prefix='/api/artifacts', tags=['分析产物'])


@router.get('/{artifact_id}')
async def download_artifact(artifact_id: str, format: str = 'original', user=Depends(current_user), session=Depends(get_session)):
    """确认文件属于当前用户的任务，并已在任务中登记后提供下载。表格也可以下载为 CSV。"""
    try:
        info, path = read_artifact(artifact_id)
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, '产物不存在') from None
    task = await owned_task(session, info['task_id'], user.id)
    if not any(item['id'] == artifact_id for item in task.artifacts):
        raise HTTPException(404, '产物尚未发布')
    if format == 'csv' and info['kind'] == 'table':
        path = path.parent / 'download.csv'
    elif format != 'original':
        raise HTTPException(422, '下载格式无效')
    return FileResponse(path, filename=f"{info['kind']}-{artifact_id[:8]}{path.suffix}",
                        headers={'X-Content-Type-Options': 'nosniff'})
