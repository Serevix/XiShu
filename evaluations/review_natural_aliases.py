"""检查自然语言题中的列名含义，并独立重算数值。原始自动评分保持不变。"""
import argparse
import json
import hashlib
from pathlib import Path
from evaluations.oracle import compare_rows, reference_answers
from evaluations.benchmark import candidates_correct
from evaluations.generalization import varied_tables

# 根据已保存 SQL 的聚合表达式确认每列含义，不因为数值刚好相等就猜测列名对应关系。
REVIEWED = {
    'natural_overall': {'valid_orders': 'order_count', 'total_amount': 'item_amount', 'total_freight': 'freight_amount'},
    'natural_categories': {'category_name': 'category', 'amount': 'item_amount', 'orders': 'order_count', 'amount_pct': 'share_percent'},
    'natural_monthly_reword': {'month': 'month', 'amount': 'item_amount', 'orders': 'order_count'},
}
REFERENCES = {'natural_overall': 'summary', 'natural_categories': 'category', 'natural_monthly_reword': 'monthly'}


def main():
    """复核三道原评分未识别列名的题目并保存证据，执行失败的任务仍算交付失败。"""
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('evaluations/results/acceptance-natural-20261010'))
    args = parser.parse_args()
    data = json.loads((args.output / 'results.json').read_text(encoding='utf-8'))
    tables = varied_tables(321, 193)
    assert hashlib.sha256(json.dumps(tables, sort_keys=True).encode()).hexdigest() == data['imports'][0]['raw_sha256']
    reference = reference_answers(tables)
    review = {'method': 'human-reviewed SQL column meanings + independent Decimal oracle; original scores retained', 'cases': []}
    for case in data['cases']:
        candidates = case.get('table_checks', [])
        if case['id'] in REVIEWED:
            mapping, candidates = REVIEWED[case['id']], []
            expected = reference[REFERENCES[case['id']]]
            for artifact in case['downloads']:
                if artifact['kind'] != 'table':
                    continue
                body = json.loads((args.output / case['id'] / (artifact['id'] + '.json')).read_text(encoding='utf-8'))
                if set(mapping) <= set(body['columns']):
                    rows = [{mapping[key]: row[key] for key in mapping} for row in body['rows']]
                    candidates.append({'artifact_id': artifact['id'], 'aliases': mapping, 'rows': rows, **compare_rows(rows, expected)})
        numeric = candidates_correct(candidates)
        delivered = case['status'] == 'succeeded' and bool(case.get('downloads')) and all(a['passed'] for a in case['downloads'])
        delivered = delivered and ('report' in case.get('artifact_kinds', [])) and case.get('sse_replay_passed', False)
        tools = not case['id'] in ('natural_monthly', 'natural_categories') or 'render_chart' in case['tools']
        if case['id'] == 'clarified_repeat':
            tools = case['clarification_observed'] and 'customer_repeat_metrics' in case['tools']
        review['cases'].append({'id': case['id'], 'original_automatic_passed': case['passed'], 'numeric_passed': numeric,
                                'semantic_delivery_passed': bool(numeric and delivered and tools), 'table_checks': candidates})
    (args.output / 'alias-review.json').write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps([{key: row[key] for key in ('id','original_automatic_passed','numeric_passed','semantic_delivery_passed')} for row in review['cases']], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
