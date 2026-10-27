"""在无网络的受限容器中运行分析代码，只提供当前任务授权的一份 CSV。"""
import json
import math
from decimal import Decimal
from pathlib import Path
import numpy as np
import pandas as pd


def serialize_result(value):
    """将允许的结果类型转换为表格。结果过大、包含非有限数值或不能序列化时，返回错误。"""
    if isinstance(value, pd.Series):
        value = value.reset_index()
    if not isinstance(value, pd.DataFrame):
        value = pd.DataFrame(value if isinstance(value, list) else [value])
    if len(value) > 1000 or len(value.columns) > 40:
        raise ValueError('Python 结果超出大小限制')
    if value.columns.duplicated().any():
        raise ValueError('Python 结果列名重复')
    body = json.loads(value.to_json(orient='split', date_format='iso', force_ascii=False))
    return {'columns': [str(column) for column in body['columns']],
            'rows': [dict(zip(map(str, body['columns']), row)) for row in body['data']]}


def main() -> None:
    """从固定路径读取数据和代码，使用受限内置函数执行代码，随后保存表格结果。"""
    code = Path('/input/code.py').read_text(encoding='utf-8')
    frame = pd.read_csv('/input/data.csv')
    allowed = {'len': len, 'range': range, 'sum': sum, 'min': min, 'max': max, 'round': round,
               'str': str, 'int': int, 'float': float, 'bool': bool, 'list': list, 'dict': dict,
               'tuple': tuple, 'sorted': sorted, 'abs': abs, 'enumerate': enumerate, 'zip': zip}
    scope = {'__builtins__': allowed, 'df': frame, 'pd': pd, 'np': np, 'math': math, 'Decimal': Decimal}
    exec(compile(code, '<analysis>', 'exec'), scope, scope)
    result = serialize_result(scope['result'])
    Path('/output/result.json').write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf-8')


if __name__ == '__main__':
    main()
