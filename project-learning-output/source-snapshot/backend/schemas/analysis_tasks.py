"""定义创建分析任务和提交澄清答案的请求格式。"""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict


class TaskRequest(BaseModel):
    """定义分析问题、数据集、可选会话和运行模式等创建任务所需的字段。"""
    model_config = ConfigDict(str_strip_whitespace=True)
    dataset_id: UUID
    question: str = Field(min_length=2, max_length=4000)
    mode: Literal['demo', 'llm'] | None = None
    conversation_id: UUID | None = None


class ResumeRequest(BaseModel):
    """定义恢复任务时提交的补充答案，并拒绝纯空白内容。"""
    model_config = ConfigDict(str_strip_whitespace=True)
    answer: str = Field(min_length=1, max_length=2000)
