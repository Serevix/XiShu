"""在 Docker 容器中执行分析代码，使用参数列表启动子进程，不把代码拼成 shell 命令。"""
import asyncio
import json
import subprocess
from backend.core.config import get_settings
from backend.core.storage import artifact_dir, read_table, write_table
from backend.models.base import new_id
from backend.execution.policy import validate_python


async def execute_python(task_id: str, artifact_id: str, code: str) -> dict:
    """只向容器提供本次分析所需的输入和输出目录。执行超时或被取消时，尝试强制删除容器。"""
    validate_python(code)
    read_table(artifact_id, task_id)
    root = get_settings().storage / 'sandbox-runs' / new_id()
    inputs, outputs = root / 'input', root / 'output'
    inputs.mkdir(parents=True)
    outputs.mkdir()
    # 只复制已经确认属于当前任务的输入 CSV，不把整个结果目录挂载到容器中。
    (inputs / 'data.csv').write_bytes((artifact_dir(artifact_id) / 'download.csv').read_bytes())
    (inputs / 'code.py').write_text(code, encoding='utf-8')
    container = 'analytics-' + new_id()
    # 容器禁用网络，根目录只读，并限制内存、CPU 和进程数。只有本次输出目录允许写入。
    # docker 命令通过参数列表执行，不经过 shell。模型代码写入输入文件，不参与命令拼接。
    command = ['docker', 'run', '--rm', '--name', container, '--network', 'none', '--read-only',
               '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pids-limit=64',
               '--memory=512m', '--cpus=1', '--tmpfs', '/tmp:rw,noexec,nosuid,size=32m',
               '--mount', f'type=bind,source={inputs.resolve()},target=/input,readonly',
               '--mount', f'type=bind,source={outputs.resolve()},target=/output', get_settings().sandbox_image]
    try:
        completed = await asyncio.to_thread(subprocess.run, command, capture_output=True, timeout=45)
        if completed.returncode:
            raise ValueError('Python 沙箱执行失败：' + completed.stderr.decode('utf-8', errors='replace')[-1200:])
        output = outputs / 'result.json'
        if not output.is_file() or output.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('沙箱结果缺失或过大')
        body = json.loads(output.read_text(encoding='utf-8'))
        return write_table(task_id, 'Python 分析结果', body['columns'], body['rows'], python=code, source_id=artifact_id)
    except subprocess.TimeoutExpired:
        raise ValueError('Python 执行超过时限') from None
    finally:
        # 取消 asyncio 等待不一定能结束线程中的子进程，所以无论成功、失败还是取消，都尝试删除本次容器。
        await asyncio.to_thread(subprocess.run, ['docker', 'rm', '-f', container], capture_output=True, timeout=10)
