"""按文件 ID 管理结果文件。客户端不能直接提供服务器文件路径。"""
import csv
import io
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID
from backend.core.config import get_settings
from backend.models.base import new_id


def json_value(value):
    """把金额、日期和容器类型转换为可保存为 JSON 的值，同时保留 Decimal 金额精度。"""
    if isinstance(value, Decimal):
        # 将 Decimal 金额转成字符串再写 JSON，以保留精确的小数值。绘图时再按需要转成数值。
        return format(value, 'f')
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    return value


def artifact_dir(artifact_id: str) -> Path:
    """检查文件 ID 是否为合法 UUID，并返回配置存储目录下的对应文件夹。"""
    key = str(UUID(artifact_id))
    return get_settings().storage / 'artifacts' / key


def write_artifact(task_id: str, kind: str, label: str, content: bytes,
                   extension: str, **metadata) -> dict:
    """创建结果文件夹，先写临时文件再替换内容文件，随后保存文件类型、归属和其他描述信息。"""
    key = new_id()
    folder = artifact_dir(key)
    folder.mkdir(parents=True, exist_ok=False)
    filename = f'content.{extension}'
    temporary = folder / 'content.tmp'
    # 先写完临时文件再替换目标文件，避免读取到只写了一部分的内容。工具节点随后把文件信息登记到数据库。
    temporary.write_bytes(content)
    temporary.replace(folder / filename)
    info = {'id': key, 'task_id': task_id, 'kind': kind, 'label': label,
            'filename': filename, **json_value(metadata)}
    (folder / 'meta.json').write_text(json.dumps(info, ensure_ascii=False), encoding='utf-8')
    return info


def write_table(task_id: str, label: str, columns: list[str], rows: list[dict], **metadata) -> dict:
    """把完整结果保存为 JSON，同时生成可下载的 UTF-8 CSV，并返回结果信息和有限行预览。"""
    rows = json_value(rows)
    body = {'columns': columns, 'rows': rows}
    # preview 仅用于页面和模型预览，完整结果另存为 JSON 和 CSV。报告构建和校验会读取完整文件。
    info = write_artifact(task_id, 'table', label,
                          json.dumps(body, ensure_ascii=False).encode(), 'json',
                          columns=columns, row_count=len(rows), preview=rows[:20], **metadata)
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        safe = {key: csv_safe(value) for key, value in row.items()}
        writer.writerow(safe)
    (artifact_dir(info['id']) / 'download.csv').write_bytes(stream.getvalue().encode('utf-8-sig'))
    return info


def csv_safe(value):
    """转义可能被 Excel 当作公式执行的文本，同时保留合法的负数文本。"""
    if isinstance(value, str) and value.startswith(('=', '+', '-', '@')):
        try:
            if Decimal(value).is_finite():
                return value
        except __import__('decimal').InvalidOperation:
            pass
        return "'" + value
    return value


def read_artifact(artifact_id: str, task_id: str | None = None) -> tuple[dict, Path]:
    """读取结果文件的信息和路径。如果指定任务 ID，还要确认文件属于该任务。"""
    folder = artifact_dir(artifact_id)
    info = json.loads((folder / 'meta.json').read_text(encoding='utf-8'))
    # UUID 校验防止非法路径，任务归属检查防止引用其他任务的文件。这两项检查都需要保留。
    if task_id is not None and info['task_id'] != task_id:
        raise ValueError('不能引用其他任务的产物')
    return info, folder / info['filename']


def read_table(artifact_id: str, task_id: str) -> dict:
    """读取当前任务的表格文件。文件属于其他任务或不是表格时，拒绝读取。"""
    info, path = read_artifact(artifact_id, task_id)
    if info['kind'] != 'table':
        raise ValueError('需要表格产物 ID')
    return json.loads(path.read_text(encoding='utf-8'))
