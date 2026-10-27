"""在修复版本上重新验证相同数据和原始问题，首轮失败结果不被覆盖。"""
import argparse
import json
from pathlib import Path
from evaluations.benchmark import Benchmark, environment_record
from evaluations.generalization import varied_tables
from evaluations.oracle import reference_answers
from evaluations.oracle import compare_rows
from decimal import Decimal


def check_monthly_supplement(output: Path, case: dict, reference: dict) -> list[dict]:
    """独立检查月度报告额外计算的差值与比例，包括中间表，避免整数除法错误被漏判。"""
    months = sorted(reference['monthly'], key=lambda row: row['month'])
    targets = {}
    previous = None
    for row in months:
        amount_diff = row['item_amount'] - previous['item_amount'] if previous else None
        order_diff = row['order_count'] - previous['order_count'] if previous else None
        targets[row['month']] = {'amount_diff': amount_diff, 'orders_diff': order_diff,
                                 'amount_pct': amount_diff / previous['item_amount'] * 100 if previous and previous['item_amount'] else None,
                                 'orders_pct': Decimal(order_diff) / previous['order_count'] * 100 if previous and previous['order_count'] else None}
        previous = row
    aliases = {'sales_diff': 'amount_diff', 'sales_change': 'amount_diff', 'amount_diff': 'amount_diff',
               'sales_mom_pct': 'amount_pct', 'amount_pct': 'amount_pct', 'sales_change_pct': 'amount_pct',
               'order_diff': 'orders_diff', 'orders_diff': 'orders_diff', 'order_change': 'orders_diff',
               'order_mom_pct': 'orders_pct', 'orders_pct': 'orders_pct', 'order_change_pct': 'orders_pct',
               'amount_growth': 'amount_pct', 'order_growth': 'orders_pct', 'order_pct': 'orders_pct'}
    checks = []
    for artifact in case.get('downloads', []):
        if artifact['kind'] != 'table':
            continue
        body = json.loads((output / case['id'] / (artifact['id'] + '.json')).read_text(encoding='utf-8'))
        month_column = next((column for column in ('month', 'purchase_month') if column in body['columns']), None)
        selected = {column: aliases[column] for column in body['columns'] if column in aliases}
        if not month_column or not selected:
            continue
        actual = [{'month': row[month_column], **{key: row[column] for column, key in selected.items()}} for row in body['rows']]
        expected = [{'month': month, **{key: values[key] for key in selected.values()}} for month, values in targets.items()]
        checks.append({'artifact_id': artifact['id'], 'checked_columns': list(selected), **compare_rows(actual, expected)})
    return checks


def main():
    """对失败的自然语言月度题复测两次，单独记录修复后结果，不修改首轮准确率。"""
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8001')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    benchmark = Benchmark(args.url, args.output, 'llm')
    benchmark.result['environment'] = {**environment_record(), 'data_seed': 321,
                                       'data_source': 'same synthetic distribution as failed natural question'}
    benchmark.register()
    tables = varied_tables(321, 193)
    dataset = benchmark.upload('失败自然题最终版本重测', tables)
    if dataset['status'] != 'ready':
        raise RuntimeError('重测数据导入失败')
    question = '每个月的有效订单销售额和订单量怎么样？按月份画一张销售额折线图，给我看看走势。'
    aliases = {'amount': 'item_amount', 'sales_amount': 'item_amount', 'total_sales': 'item_amount',
               'sales': 'item_amount', 'orders': 'order_count', 'total_orders': 'order_count',
               'orders_count': 'order_count', 'valid_orders': 'order_count', 'purchase_month': 'month'}
    for index in (1, 2):
        reference = reference_answers(tables)
        case = benchmark.run_case({'id': f'monthly_after_fix_{index}', 'reference': 'monthly', 'question': question,
                                   'column_aliases': aliases, 'required_tools': ['render_chart']},
                                  dataset['id'], reference, 'after_fix')
        checks = check_monthly_supplement(args.output, case, reference)
        case['supplemental_checks'] = checks
        case['supplemental_passed'] = all(check['passed'] for check in checks)
        case['passed'] = case['passed'] and case['supplemental_passed']
        benchmark.checkpoint()
        print('补充派生指标核对：' + str(case['supplemental_passed']), flush=True)
    benchmark.finish_summary()
    benchmark.client.close()
    print(json.dumps(benchmark.result['summary'], ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
