"""定义导入确认请求。字段映射的键是项目目标字段，值是上传文件的原始列名。"""
from typing import Literal
from pydantic import BaseModel, Field


class ImportRequest(BaseModel):
    """定义导入时确认的字段映射、币种和有效订单状态等信息。"""
    mapping: dict[str, dict[str, str]] = Field(default_factory=dict)
    sheets: dict[str, str] = Field(default_factory=dict)
    encoding: Literal['utf-8-sig', 'gb18030'] = 'utf-8-sig'
    currency: str = Field(default='BRL', pattern=r'^[A-Z]{3}$')
    valid_status: str = Field(default='delivered', min_length=1, max_length=30)
