"""读取数据集时同时检查它是否属于当前用户。"""
from fastapi import HTTPException
from sqlalchemy import select
from backend.models.dataset import Dataset


async def owned_dataset(session, dataset_id: str, user_id: str) -> Dataset:
    """查找当前用户的数据集。不存在或属于其他用户时，统一返回数据集不存在错误。"""
    obj = await session.scalar(select(Dataset).where(Dataset.id == dataset_id, Dataset.user_id == user_id))
    if obj is None:
        raise HTTPException(404, '数据集不存在')
    return obj
