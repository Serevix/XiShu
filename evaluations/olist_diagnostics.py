"""复核已保存的 Olist 分页结果、图表来源及慢查询，不改写首轮原始评分。"""
import argparse
import asyncio
import csv
import hashlib
import json
import time
from pathlib import Path
from decimal import Decimal
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from backend.core.config import get_settings
from backend.core.database import Session
from backend.ingestion.mapping import default_mapping
from backend.execution.policy import validate_sql
from backend.execution.metric_scope import scoped_query
from evaluations.olist_real import real_reference
from evaluations.oracle import compare_rows


def reference_from_sample(output: Path) -> dict:
    """重新读取四张官方样本 CSV 并独立计算基准，不根据 Agent 的结果推算标准答案。"""
    tables = {}
    for table in ('customers', 'products', 'orders', 'order_items'):
        with (output / 'sample' / f'olist_{table}_dataset.csv').open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        mapping = default_mapping(list(rows[0]), table)
        tables[table] = [{field: row[column] for field, column in mapping.items()} for row in rows]
    return real_reference(tables)


def additional_metrics(output: Path, reference: dict) -> list[dict]:
    """额外检查结果中出现的差值、地区比例和汇总指标，原始目标字段评分仍保持不变。"""
    monthly = []
    previous = None
    for row in sorted(reference['monthly'], key=lambda item: item['month']):
        difference = row['item_amount'] - previous['item_amount'] if previous else None
        growth = difference / previous['item_amount'] * 100 if previous else None
        monthly.append({'month': row['month'], 'amount_diff': difference, 'amount_growth': growth,
                        'amount_mom_pct': growth, 'order_diff': row['order_count'] - previous['order_count'] if previous else None,
                        'avg_order_value': row['item_amount'] / row['order_count']})
        previous = row
    total = reference['summary'][0]
    regions = {row['state']: row for row in reference['region']}
    shares = [{'state': row['state'], 'amount_pct': row['item_amount'] / total['item_amount'] * 100,
               'order_pct': Decimal(row['order_count']) / total['order_count'] * 100} for row in regions.values()]
    top3_amount = sum(regions[state]['item_amount'] for state in ('SP', 'RJ', 'MG'))
    top3_orders = sum(regions[state]['order_count'] for state in ('SP', 'RJ', 'MG'))
    scalars = {'sp_rj_ratio': regions['SP']['item_amount'] / regions['RJ']['item_amount'],
               'total_with_freight': reference['aov'][0]['total_amount'],
               'min_month': monthly[0]['month'], 'max_month': monthly[-1]['month'],
               'top3_amount': top3_amount, 'top3_orders': top3_orders,
               'total_amount': total['item_amount'], 'total_orders': total['order_count'],
               'top3_amount_pct': top3_amount / total['item_amount'] * 100,
               'top3_order_pct': Decimal(top3_orders) / total['order_count'] * 100}
    checks = []
    for task_path in sorted(output.glob('*/task.json')):
        task = json.loads(task_path.read_text(encoding='utf-8'))
        for artifact in task['artifacts']:
            if artifact['kind'] != 'table':
                continue
            columns = set(artifact['columns'])
            expected = None
            if 'month' in columns:
                selected = columns & (set(monthly[0]) - {'month'})
                if selected:
                    expected = [{key: row[key] for key in {'month', *selected}} for row in monthly]
            elif 'state' in columns:
                selected = columns & {'amount_pct', 'order_pct'}
                if selected:
                    expected = [{key: row[key] for key in {'state', *selected}} for row in shares]
            elif 'total_with_freight' in columns:
                expected = [{'total_with_freight': scalars['total_with_freight']}]
            elif columns <= scalars.keys() and columns & {'sp_rj_ratio', 'min_month', 'top3_amount', 'top3_amount_pct'}:
                expected = [{key: scalars[key] for key in columns}]
            if expected is not None:
                body = json.loads((task_path.parent / (artifact['id'] + '.json')).read_text(encoding='utf-8'))
                checks.append({'case_id': task_path.parent.name, 'artifact_id': artifact['id'],
                               'columns_checked': sorted(expected[0]), **compare_rows(body['rows'], expected)})
    return checks


async def inspect_plan(output: Path, result: dict) -> dict:
    """在只读事务中对相关慢查询执行 EXPLAIN，并记录当前统计信息的更新时间。结果不代表超时时的原始计划。"""
    task = json.loads((output / 'summary/task.json').read_text(encoding='utf-8'))
    query = next(event['payload']['arguments']['query'] for event in task['events'] if event['type'] == 'tool_started')
    statement = scoped_query(validate_sql(query), 'delivered')
    async with Session() as session:
        cursor = await session.execute(text("SELECT relname,n_live_tup,last_analyze,last_autoanalyze FROM pg_stat_user_tables WHERE relname IN ('customers','products','orders','order_items') ORDER BY relname"))
        stats = [dict(row) for row in cursor.mappings()]
    reader = create_async_engine(get_settings().sql_reader_url, poolclass=NullPool)
    try:
        async with reader.begin() as connection:
            await connection.execute(text('SET TRANSACTION READ ONLY'))
            await connection.execute(text("SELECT set_config('app.dataset_id', :id, true)"), {'id': result['datasets']['main']})
            await connection.execute(text("SELECT set_config('statement_timeout', '10000', true)"))
            started = time.monotonic()
            plan = await connection.scalar(text('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + statement), {'analysis_valid_status': 'delivered'})
            return {'query': query, 'effective_sql': statement, 'seconds': round(time.monotonic() - started, 4),
                    'current_table_statistics': stats, 'plan': plan,
                    'limitation': '这是首轮结束后的当前计划；未捕获首次超时执行计划，不能据此确定历史慢查询根因。'}
    except Exception as error:
        return {'query': query, 'current_table_statistics': stats, 'error_type': type(error).__name__}
    finally:
        await reader.dispose()


def audit(output: Path) -> dict:
    """根据原始样本重新计算答案，检查两张完整州表，以及明确使用 OFFSET 的七行分页结果。"""
    result = json.loads((output / 'results.json').read_text(encoding='utf-8'))
    hashes = {table: hashlib.sha256((output / 'sample' / f'olist_{table}_dataset.csv').read_bytes()).hexdigest()
              for table in ('customers', 'products', 'orders', 'order_items')}
    if hashes != result['provenance']['sample_csv_sha256']:
        raise ValueError('样本哈希与首轮记录不一致，不能据此复核首轮结果')
    reference = reference_from_sample(output)
    expected = reference['region']
    task = json.loads((output / 'natural_regions/task.json').read_text(encoding='utf-8'))
    checks = []
    for artifact in task['artifacts']:
        if artifact['kind'] != 'table':
            continue
        body = json.loads((output / 'natural_regions' / (artifact['id'] + '.json')).read_text(encoding='utf-8'))
        target = sorted(expected, key=lambda row: row['item_amount'], reverse=True)[20:] if 'OFFSET 20' in artifact['sql'] else expected
        observed = [{'state': row['state'], 'order_count': row['order_count'], 'item_amount': row['sales_amount']} for row in body['rows']]
        checks.append({'artifact_id': artifact['id'], 'explicit_offset': 'OFFSET 20' in artifact['sql'],
                       'rows': len(observed), **compare_rows(observed, target)})
    coverage = any(check['passed'] and not check['explicit_offset'] and check['rows'] == len(expected) for check in checks)
    passed = coverage and all(check['passed'] for check in checks)
    single = [case for case in result['cases'] if case['phase'] == 'single']
    charts = []
    for case in result['cases']:
        task_path = output / case['id'] / 'task.json'
        if not task_path.exists():
            continue
        data = json.loads(task_path.read_text(encoding='utf-8'))
        for chart in data['artifacts']:
            if chart['kind'] != 'chart':
                continue
            source = json.loads((output / case['id'] / (chart['source_id'] + '.json')).read_text(encoding='utf-8'))
            count = len(source['rows'])
            charts.append({'case_id': case['id'], 'chart_id': chart['id'], 'source_rows': count,
                           'passed': chart['source_row_count'] == count == chart['plotted_row_count'] and chart['truncated'] is False})
    return {'sample_hashes_match': True, 'raw_summary': result['summary'],
            'natural_regions_review': {'raw_passed': False, 'reviewed_passed': passed,
                                       'reason': '报告含两张正确的完整27州表及正确的OFFSET 20补充7行；原评分把补充分页也要求为完整27行，产生假阴性。',
                                       'checks': checks, 'full_coverage': coverage},
            'reviewed_first_round': {'case_count': len(single),
                                     'numeric_correct': sum(bool(case.get('numeric_passed')) for case in single) + int(passed),
                                     'fully_passed': sum(case['passed'] for case in single) + int(passed)},
            'chart_checks': charts, 'additional_metric_checks': additional_metrics(output, reference),
            'query_plan_after_first_round': asyncio.run(inspect_plan(output, result))}


def main():
    """保存独立复核结果，不修改原始 results.json 或失败报告。"""
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--retest', type=Path)
    args = parser.parse_args()
    review = audit(args.output)
    (args.output / 'independent-review.json').write_text(json.dumps(review, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    if args.retest:
        original = json.loads((args.output / 'provenance.json').read_text(encoding='utf-8'))
        repeated = json.loads((args.retest / 'provenance.json').read_text(encoding='utf-8'))
        if original['sample_csv_sha256'] != repeated['sample_csv_sha256']:
            raise ValueError('复测样本与首轮哈希不一致')
        extra = {'same_sample_hashes': True, 'additional_metric_checks': additional_metrics(args.retest, reference_from_sample(args.output))}
        (args.retest / 'additional-review.json').write_text(json.dumps(extra, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in review.items() if key != 'query_plan_after_first_round'}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
