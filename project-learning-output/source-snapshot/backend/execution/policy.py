"""先检查 SQL 和 Python 是否包含禁止操作，再由数据库权限和容器限制实际执行。"""
import ast
import sqlglot
from sqlglot import exp
from backend.core.config import get_settings

TABLES = {'orders', 'order_items', 'products', 'customers'}
FUNCTIONS = {'COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'ROUND', 'ABS', 'COALESCE', 'NULLIF', 'CAST',
             'DATE_TRUNC', 'TIME_TO_STR', 'TO_CHAR', 'EXTRACT', 'DATE_PART', 'LOWER', 'UPPER',
             'TRIM', 'LENGTH', 'GREATEST', 'LEAST', 'CONCAT', 'CONCAT_WS', 'TS_OR_DS_TO_DATE', 'CASE', 'IF', 'LAG', 'LEAD'}


def validate_sql(query: str) -> str:
    """只允许指定业务表上的查询，并把除法的分子转为 NUMERIC，避免整数相除丢失小数。"""
    if len(query) > 16000:
        raise ValueError('SQL 超长')
    try:
        statements = sqlglot.parse(query, read='postgres')
    except sqlglot.errors.ParseError:
        raise ValueError('SQL 语法解析失败') from None
    if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.Union)):
        raise ValueError('只允许单条 SELECT 查询')
    tree = statements[0]
    # 通过语法树检查查询及其子查询中的禁止操作，不能只搜索关键词。实际数据库权限仍需单独限制。
    forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Command, exp.Into, exp.Lock,
                 exp.Copy, exp.Alter, exp.Transaction)
    if any(isinstance(node, forbidden) for node in tree.walk()):
        raise ValueError('SQL 包含不允许的操作')
    ctes = {node.alias_or_name for node in tree.find_all(exp.CTE)}
    # 允许引用查询中定义的 CTE，但真实表只能是四张业务表，不允许访问系统表、其他数据库或未知 schema。
    for table in tree.find_all(exp.Table):
        if table.name not in TABLES | ctes or table.catalog or table.db not in ('', 'public'):
            raise ValueError('只能访问当前数据集的四张业务表')
    if not any(table.name in TABLES for table in tree.find_all(exp.Table)):
        raise ValueError('分析查询必须引用业务数据')
    for function in tree.find_all(exp.Func):
        if isinstance(function, (exp.Binary, exp.Unary, exp.Predicate)):
            continue  # sqlglot 也把 AND 等运算符归入 Func，但它们不是数据库函数，因此这里跳过函数白名单检查。
        name = function.name.upper() if isinstance(function, exp.Anonymous) else function.sql_name().upper()
        # sqlglot 用 TimestampTrunc 表示 PostgreSQL 的 DATE_TRUNC，检查白名单时将名称统一为 DATE_TRUNC。
        if isinstance(function, exp.TimestampTrunc):
            name = 'DATE_TRUNC'
        if name not in FUNCTIONS:
            raise ValueError(f'函数 {name} 不在允许范围内')
    # 从最内层除法开始转换，避免嵌套计算先丢失小数。转换后的 SQL 会保存在结果信息中，便于追溯。
    for division in reversed(list(tree.find_all(exp.Div))):
        # PostgreSQL 的整数除法中 1/2 得到 0。先将分子转换为 NUMERIC，再执行除法，才能保留小数结果。
        division.set('this', exp.Cast(this=division.this.copy(), to=exp.DataType.build('NUMERIC')))
    return tree.sql(dialect='postgres').rstrip(';')


def validate_python(code: str) -> None:
    """检查代码语法、名称和属性，拒绝危险操作。通过这项检查后，代码仍须放在受限容器中执行。"""
    if len(code) > 12000:
        raise ValueError('Python 代码超长')
    tree = ast.parse(code)
    # 语法检查只能拒绝部分危险写法，不能代替隔离环境。执行时仍要限制容器的网络、文件访问和资源使用。
    banned_names = {'open', 'exec', 'eval', 'compile', 'getattr', 'setattr', '__import__', 'globals', 'locals', 'input', 'print'}
    banned_attrs = {'system', 'popen', 'read_pickle', 'read_sql', 'read_csv', 'read_excel', 'to_pickle', 'save', 'load', 'eval'}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.With, ast.AsyncWith, ast.FunctionDef, ast.ClassDef)):
            raise ValueError('使用预置的 df/pd/np/Decimal，不允许导入或定义函数和类')
        if isinstance(node, ast.Name) and (node.id in banned_names or node.id.startswith('_')):
            raise ValueError('代码包含受限名称')
        if isinstance(node, ast.Attribute) and (node.attr.startswith('_') or node.attr in banned_attrs):
            raise ValueError('代码包含受限属性')
    if not any(isinstance(node, ast.Name) and node.id == 'result' and isinstance(node.ctx, ast.Store) for node in ast.walk(tree)):
        raise ValueError('请将最终结果赋值给 result')
