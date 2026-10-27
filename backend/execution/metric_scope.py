"""在查询中强制使用任务确认的有效订单状态，不依赖模型主动添加正确的 WHERE 条件。"""
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope


def scoped_query(query: str, valid_status: str) -> str:
    """找到查询中实际引用的订单和明细表，将它们改为只包含有效订单的子查询，并绑定状态参数。"""
    tree = sqlglot.parse_one(query, read='postgres')
    sources = []
    # 根据 SQL 作用域区分真实表和同名 CTE。不能直接替换字符串，否则可能误改别名或子查询。
    for scope in traverse_scope(tree):
        for _, (_, source) in scope.selected_sources.items():
            if isinstance(source, exp.Table) and source.name in ('orders', 'order_items'):
                sources.append(source)
    # 模型指定的状态与当前口径冲突时直接报错，让模型修正，而不是悄悄返回空结果。
    for comparison in tree.find_all(exp.EQ):
        left, right = comparison.this, comparison.expression
        if isinstance(right, exp.Column):
            left, right = right, left
        if isinstance(left, exp.Column) and left.name == 'status' and isinstance(right, exp.Literal) and right.is_string:
            if right.this != valid_status and sources:
                raise ValueError(f'订单状态与已确认口径冲突：当前有效状态为 {valid_status}，请移除冲突条件；执行器自动限定有效订单。')
    for source in sources:
        alias = source.args.get('alias') or exp.TableAlias(this=source.this.copy())
        if source.name == 'orders':
            template = 'SELECT * FROM public.orders WHERE status=:analysis_valid_status'
        else:
            # 明细表没有状态列，所以用 EXISTS 查询其订单是否有效，同时匹配数据集 ID，避免关联到其他数据集。
            template = '''SELECT scoped_items.* FROM public.order_items AS scoped_items
                WHERE EXISTS (SELECT 1 FROM public.orders AS scoped_orders
                WHERE scoped_orders.dataset_id=scoped_items.dataset_id
                  AND scoped_orders.order_id=scoped_items.order_id
                  AND scoped_orders.status=:analysis_valid_status)'''
        filtered = sqlglot.parse_one(template, read='postgres')
        # sqlglot 默认生成 psycopg 格式的占位符，但 SQLAlchemy text 使用冒号参数，因此在这里转换格式。
        for placeholder in list(filtered.find_all(exp.Placeholder)):
            placeholder.replace(exp.Var(this=':analysis_valid_status'))
        source.replace(exp.Subquery(this=filtered, alias=alias.copy()))
    for column in tree.find_all(exp.Column):
        if column.db == 'public':
            column.set('db', None)
    return tree.sql(dialect='postgres')
