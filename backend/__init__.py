"""初始化后端包，并为 Windows 设置 psycopg 支持的异步事件循环。"""
import asyncio
import sys

# psycopg 不支持 Windows 默认的 Proactor 事件循环，因此改用 Selector。容器命令在线程中执行。
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
