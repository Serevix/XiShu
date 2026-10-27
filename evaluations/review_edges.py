"""离线复核已保存的边界题结果，不再次请求模型，也不修改原始结果文件。"""
import argparse
import json
from pathlib import Path
from evaluations.benchmark import Benchmark
from evaluations.oracle import compare_rows, reference_answers
from scripts.generate_demo_data import generate_tables


def main():
    """保存原始评分、列名对应关系和图表行数检查结果，区分命名差异与实际错误。"""
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    path = args.directory / 'results.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    natural = next(item for item in data['cases'] if item['id'] == 'natural_summary')
    data.setdefault('original_scoring', {'natural_summary_passed': natural['passed'],
                                        'all_customer_chart_passed': next(item for item in data['cases'] if item['id'] == 'all_customer_chart')['passed'],
                                        'summary': data['summary']})
    aliases = {'total_orders': 'order_count', 'total_amount': 'item_amount', 'total_freight': 'freight_amount'}
    expected = reference_answers(generate_tables())['summary']
    checks = []
    task = json.loads((args.directory / 'natural_summary/task.json').read_text(encoding='utf-8'))
    for artifact in task['artifacts']:
        if artifact['kind'] == 'table':
            body = json.loads((args.directory / 'natural_summary' / (artifact['id'] + '.json')).read_text(encoding='utf-8'))
            rows = [{aliases.get(key, key): value for key, value in row.items()} for row in body['rows']]
            if set(expected[0]) <= {aliases.get(key, key) for key in body['columns']}:
                checks.append({'artifact_id': artifact['id'], 'column_aliases': aliases, 'rows': rows, **compare_rows(rows, expected)})
    natural['table_checks'] = checks
    natural['numeric_passed'] = bool(checks and checks[-1]['passed'])
    natural['missing_fields'] = [] if checks else list(expected[0])
    natural['passed'] = natural['status'] == 'succeeded' and natural['numeric_passed'] and natural['delivery_passed']
    chart = next(item for item in data['cases'] if item['id'] == 'all_customer_chart')
    chart['chart_coverage_passed'] = bool(data['chart_coverage']) and all(item['complete'] for item in data['chart_coverage'])
    chart['passed'] = chart['passed'] and chart['chart_coverage_passed']
    data['scoring_review'] = ['自然题没有规定列名；SQL 和实际数值人工核对后，以显式别名映射离线复核，原始表格未改。',
                              '全部客户题表格数值正确，但图表缺少部分客户，整题交付判为失败。',
                              '本复核不改变首轮十一题的九题正确结论，不重新调用模型。']
    benchmark = Benchmark('http://127.0.0.1:8001', args.directory, 'llm')
    benchmark.result = data
    benchmark.finish_summary()
    benchmark.client.close()
    print(json.dumps(data['summary'], ensure_ascii=False))


if __name__ == '__main__':
    main()
