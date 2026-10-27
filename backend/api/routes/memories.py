"""提供个人偏好、常问模板的查询和管理接口，每次操作都检查用户归属。"""
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from backend.api.dependencies import current_user
from backend.core.database import get_session
from backend.core.config import get_settings
from backend.models import UserMemory, QuestionPattern
from backend.memory.service import set_preferences, serialize_memory, enqueue, personalize

router = APIRouter(prefix='/api/memories', tags=['长期记忆'])


class PreferencesRequest(BaseModel):
    """定义允许修改的图表和报告偏好。此接口不接受有效订单状态或金额计算规则。"""
    model_config = ConfigDict(extra='forbid')
    chart_kind: Literal['bar','line'] | None = None
    report_style: Literal['concise','detailed'] | None = None


@router.get('')
async def list_memories(user=Depends(current_user), session=Depends(get_session)):
    """返回当前用户保存的偏好、常问模板及向量同步状态。"""
    preferences = list(await session.scalars(select(UserMemory).where(UserMemory.user_id == user.id, UserMemory.deleted.is_(False))))
    patterns = list(await session.scalars(select(QuestionPattern).where(QuestionPattern.user_id == user.id, QuestionPattern.deleted.is_(False)).order_by(QuestionPattern.use_count.desc()).limit(50)))
    return {'preferences':[serialize_memory(row,'preference') for row in preferences],
            'patterns':[serialize_memory(row,'pattern') for row in patterns], 'enabled':get_settings().memory_enabled}


@router.put('/preferences')
async def update_preferences(payload: PreferencesRequest, user=Depends(current_user)):
    """只更新请求中提供的偏好。未提供的字段保持原值，传入 null 则删除该项偏好。"""
    return await set_preferences(user.id, payload.model_dump(exclude_unset=True))


@router.get('/suggestions')
async def suggestions(question: str = Query('', max_length=4000), user=Depends(current_user), session=Depends(get_session)):
    """有问题文本时检索相关模板；没有问题文本时推荐常用模板。模板中的旧日期需要用户重新填写。"""
    if question:
        return await personalize(user.id, question)
    rows = list(await session.scalars(select(QuestionPattern).where(QuestionPattern.user_id == user.id, QuestionPattern.deleted.is_(False),
                       QuestionPattern.use_count >= get_settings().memory_pattern_min_uses).order_by(QuestionPattern.use_count.desc()).limit(5)))
    return {'patterns':[{'id':row.id,'template':row.template,'use_count':row.use_count} for row in rows]}


@router.delete('/{kind}/{identifier}')
async def forget(kind: Literal['preference','pattern'], identifier: str, user=Depends(current_user), session=Depends(get_session)):
    """先在 PostgreSQL 中标记记忆已删除，使其立即停止使用，再异步删除向量。向量删除失败会重试。"""
    model = UserMemory if kind == 'preference' else QuestionPattern
    row = await session.scalar(select(model).where(model.id == identifier, model.user_id == user.id).with_for_update())
    if row is None:
        raise HTTPException(404, '记忆不存在')
    # 先标记删除并增加版本号。检索时会排除这条记录，即使 Milvus 的向量还没删掉，也不会使用旧记忆。
    row.deleted = True
    row.version += 1
    row.sync_status, row.sync_error = 'pending', None
    await session.commit()
    enqueue(kind + ':' + row.id)
    return {'deleted':True, 'vector_sync':'pending'}
