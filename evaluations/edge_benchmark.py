"""补测自然语言问题、零分母和较多分类的图表，结果与首轮固定题分别统计。"""
import argparse
import json
from collections import defaultdict
from decimal import Decimal
from datetime import datetime
from pathlib import Path
from evaluations.benchmark import Benchmark, environment_record
from evaluations.oracle import reference_answers
from scripts.generate_demo_data import generate_tables


def main():
    """使用独立账号和数据集运行边界题，并检查图表是否包含来源表全部行。"""
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    parser.add_argument('--url', default='http://127.0.0.1:8001')
    args = parser.parse_args()
    args.output = args.output or Path('evaluations/results') / ('edges-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    args.output.mkdir(parents=True, exist_ok=False)
    benchmark = Benchmark(args.url, args.output, 'llm')
    benchmark.result['environment'] = environment_record()
    benchmark.register()
    tables = generate_tables()
    dataset = benchmark.upload('自然问题与多分类图', tables)
    benchmark.run_case({'id': 'natural_summary', 'reference': 'summary',
                        'column_aliases': {'total_orders': 'order_count', 'total_amount': 'item_amount', 'total_freight': 'freight_amount'},
                        'question': '帮我看一下这份数据的整体销售表现，订单多少，卖了多少钱，运费是多少？'},
                       dataset['id'], reference_answers(tables), 'natural_manual')
    valid = {row['order_id']: row['customer_id'] for row in tables['orders'] if row['status'] == 'delivered'}
    amounts = defaultdict(Decimal)
    for item in tables['order_items']:
        if item['order_id'] in valid:
            amounts[valid[item['order_id']]] += Decimal(str(item['price']))
    reference = {'customers': [{'customer_id': key, 'item_amount': value} for key, value in amounts.items()]}
    chart = benchmark.run_case({'id': 'all_customer_chart', 'reference': 'customers', 'required_tools': ['render_chart'],
                                'question': '按 customer_id 列出每位有已交付订单客户的商品销售额，列名 customer_id、item_amount。不要只取前几名，查询全部客户，绘制包含所有客户的柱状图并生成报告。'},
                               dataset['id'], reference, 'edge')
    chart_sources = []
    for item in chart.get('downloads', []):
        if item['kind'] == 'chart':
            metadata = json.loads((Path('storage/artifacts') / item['id'] / 'meta.json').read_text(encoding='utf-8'))
            source = benchmark.client.get('/api/artifacts/' + metadata['source_id']).json()
            count = len(source['rows'])
            chart_sources.append({'chart_id': item['id'], 'source_rows': len(source['rows']),
                                  'plotted_rows': metadata.get('plotted_row_count'),
                                  'complete': metadata.get('source_row_count') == count and metadata.get('plotted_row_count') == count
                                              and metadata.get('truncated') is False})
    benchmark.result['chart_coverage'] = chart_sources
    chart['chart_coverage_passed'] = bool(chart_sources) and all(item['complete'] for item in chart_sources)
    chart['passed'] = chart['passed'] and chart['chart_coverage_passed']
    benchmark.check('图表覆盖全部请求的客户', bool(chart_sources) and all(item['complete'] for item in chart_sources), chart_sources)
    zero = json.loads(json.dumps(tables))
    for item in zero['order_items']:
        item['price'] = 0
    zero_dataset = benchmark.upload('零销售额分母', zero)
    benchmark.run_case({'id': 'zero_share', 'reference': 'category',
                        'question': '按已确认有效订单状态统计各品类商品销售额、去重订单量、销售额百分比。结果列名 category、item_amount、order_count、share_percent。分母为零时占比应返回空值并解释，不能把未定义的占比说成零，生成报告。'},
                       zero_dataset['id'], reference_answers(zero), 'edge')
    # 按明确的列名对应规则检查自然语言题，结果单独统计，不加入固定题的准确率。
    benchmark.finish_summary()
    benchmark.client.close()
    print('边界证据目录：' + str(args.output.resolve()), flush=True)


if __name__ == '__main__':
    main()
