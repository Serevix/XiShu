"""定义登录和注册输入，并限制用户名和密码长度。"""
from pydantic import BaseModel, Field


class Credentials(BaseModel):
    """定义登录和注册共用的用户名、密码输入格式。"""
    username: str = Field(min_length=3, max_length=40, pattern=r'^[A-Za-z0-9_\u4e00-\u9fff-]+$')
    password: str = Field(min_length=8, max_length=128)
