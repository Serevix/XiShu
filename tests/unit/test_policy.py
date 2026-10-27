"""检查查询和代码权限，并核对真实金额含义，不只比较实现中的字符串。"""
import ast
from decimal import Decimal
from pathlib import Path
import pandas as pd
import pytest
from backend.execution.policy import validate_sql, validate_python
from backend.ingestion.validation import normalize_table, validate_relations
from backend.ingestion.mapping import FIELDS
from backend.core.storage import csv_safe
from scripts.generate_demo_data import generate_tables


@pytest.mark.parametrize('query', [
    'DELETE FROM orders', 'SELECT * FROM users', 'SELECT * FROM pg_catalog.pg_authid',
    "SELECT pg_read_file('/etc/passwd') FROM orders", "SELECT set_config('app.dataset_id','other',true) FROM orders",
    'SELECT * INTO copied FROM orders', 'SELECT * FROM orders; DROP TABLE orders',
    "WITH changed AS (DELETE FROM orders RETURNING *) SELECT * FROM changed",
])
def test_reject_unsafe_sql(query):
    """检查写入、系统表访问、修改数据集权限配置和多条 SQL 都会被拒绝。"""
    with pytest.raises(ValueError):
        validate_sql(query)


def test_accept_business_query():
    """检查正常的 CTE、聚合、CASE 和日期格式转换查询能够通过。"""
    query = "WITH data AS (SELECT TO_CHAR(purchase_time,'YYYY-MM') AS month, COUNT(*) AS n FROM orders WHERE status='delivered' GROUP BY 1) SELECT * FROM data ORDER BY n DESC"
    assert 'WITH' in validate_sql(query)
    assert 'SUM' in validate_sql('SELECT SUM(CASE WHEN price > 10 THEN price ELSE 0 END) FROM order_items')
    assert 'DATE_TRUNC' in validate_sql("SELECT DATE_TRUNC('month', purchase_time),COUNT(*) FROM orders GROUP BY 1")


@pytest.mark.parametrize('code', ['import os\nresult = []', "result = open('/etc/passwd').read()", 'result = df.__class__', 'x = df.copy()'])
def test_reject_unsafe_python(code):
    """检查危险内置函数、导入、内部属性，以及没有 result 的代码都会被拒绝。"""
    with pytest.raises(ValueError):
        validate_python(code)


def test_decimal_and_duplicate_validation():
    """检查金额精度得到保留，并拒绝重复的订单明细。"""
    rows = [{'order_id': 'o', 'order_item_id': '1', 'product_id': 'p', 'price': '12.30', 'freight_value': '1.20'}]
    frame = pd.DataFrame(rows)
    normalized = normalize_table(frame, 'order_items', {key:key for key in FIELDS['order_items']}, 'dataset')
    assert normalized[0]['price'] == Decimal('12.30')
    with pytest.raises(ValueError, match='主键重复'):
        normalize_table(pd.DataFrame(rows * 2), 'order_items', {key:key for key in FIELDS['order_items']}, 'dataset')


def test_synthetic_truth():
    """独立计算模拟数据的订单数和金额，检查多商品订单不会重复计入订单量。"""
    tables = generate_tables()
    normalized = {table: normalize_table(pd.DataFrame(rows), table, {key:key for key in FIELDS[table]}, 'dataset') for table, rows in tables.items()}
    validate_relations(normalized)
    ids = {row['order_id'] for row in normalized['orders'] if row['status'] == 'delivered'}
    total = sum(row['price'] for row in normalized['order_items'] if row['order_id'] in ids)
    assert len(ids) == 66
    assert total == Decimal('6550.00')


def test_csv_formula_protection():
    """检查公式文本被转义，同时负数结果仍能被 Python 正确读取。"""
    assert csv_safe('=HYPERLINK("bad")').startswith("'")
    assert csv_safe('-990.00') == '-990.00'


def test_all_python_functions_documented():
    """检查项目中每个 Python 函数都有中文说明。"""
    root = Path(__file__).resolve().parents[2]
    missing = []
    for directory in ('backend', 'scripts', 'sandbox', 'tests', 'evaluations'):
        for path in (root / directory).rglob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not ast.get_docstring(node):
                    missing.append(f'{path.relative_to(root)}:{node.lineno} {node.name}')
    assert not missing, '\n'.join(missing)
