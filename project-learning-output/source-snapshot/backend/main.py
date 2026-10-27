"""创建 FastAPI 应用，并提供前端页面和接口。数据库表由单独的初始化脚本创建。"""
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from backend.core.config import ROOT
from backend.core.database import engine
from backend.api.routes import auth, datasets, analysis_tasks, artifacts, health, memories, conversations


@asynccontextmanager
async def lifespan(app: FastAPI):
    """管理应用运行周期，在服务关闭时释放数据库资源，不在服务启动时修改数据库表。"""
    # yield 之前的代码在应用启动时执行，之后的代码在关闭时执行。数据库初始化由专门脚本负责。
    yield
    await engine.dispose()


app = FastAPI(title='可验证的电商数据分析智能体', version='0.1.0', lifespan=lifespan)
for module in (auth, datasets, analysis_tasks, artifacts, health, memories, conversations):
    app.include_router(module.router)
# 前端页面、静态文件和 API 使用同一个服务地址，页面可以通过相对路径请求接口。
app.mount('/static', StaticFiles(directory=ROOT / 'frontend'), name='static')


@app.get('/', include_in_schema=False)
async def index():
    """返回前端首页。页面和 API 使用相同地址，无需额外设置跨域登录请求。"""
    return FileResponse(ROOT / 'frontend' / 'index.html')


@app.exception_handler(ValueError)
async def invalid_value(request: Request, error: ValueError):
    """把 ValueError 转为 422 响应，并向用户返回长度受限的错误说明。"""
    return JSONResponse(status_code=422, content={'detail': str(error)[:500]})
