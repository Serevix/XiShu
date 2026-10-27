"""使用官方 Olist 在固定时间范围内的全部订单评测导入与模型分析，保存数据来源和逐题证据。"""
import argparse
import csv
import hashlib
import io
import json
import time
import zipfile
from collections import Counter
from pathlib import Path

from backend.ingestion.mapping import FIELDS, default_mapping
from evaluations.benchmark import Benchmark, environment_record, encode_json
from evaluations.natural_questions import ALIASES
from evaluations.oracle import reference_answers
from evaluations.retest_natural import check_monthly_supplement

SOURCE = 'https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce'
DOWNLOAD = 'https://www.kaggle.com/api/v1/datasets/download/olistbr/brazilian-ecommerce'
START, END = '2017-10-01', '2017-12-01'


def prepare_sample(archive: Path, output: Path) -> tuple[dict, dict]:
    """保留两个月内的全部订单及对应明细、客户和商品，包括取消订单和没有明细的订单。"""
    with zipfile.ZipFile(archive) as package:
        raw = {}
        source_hashes = {}
        for table in FIELDS:
            content = package.read(f'olist_{table}_dataset.csv')
            source_hashes[table] = hashlib.sha256(content).hexdigest()
            raw[table] = list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))
    orders = [row for row in raw['orders'] if START <= row['order_purchase_timestamp'] < END]
    ids = {row['order_id'] for row in orders}
    items = [row for row in raw['order_items'] if row['order_id'] in ids]
    customer_ids = {row['customer_id'] for row in orders}
    product_ids = {row['product_id'] for row in items}
    selected = {'orders': orders, 'order_items': items,
                'customers': [row for row in raw['customers'] if row['customer_id'] in customer_ids],
                'products': [row for row in raw['products'] if row['product_id'] in product_ids]}
    sample = output / 'sample'
    sample.mkdir()
    hashes, normalized = {}, {}
    for table, rows in selected.items():
        path = sample / f'olist_{table}_dataset.csv'
        with path.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        hashes[table] = hashlib.sha256(path.read_bytes()).hexdigest()
        mapping = default_mapping(list(rows[0]), table)
        normalized[table] = [{field: row[column] for field, column in mapping.items()} for row in rows]
    item_orders = {row['order_id'] for row in items}
    provenance = {'source_url': SOURCE, 'download_url': DOWNLOAD, 'license': 'CC BY-NC-SA 4.0',
                  'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                  'original_csv_sha256': source_hashes, 'sample_csv_sha256': hashes,
                  'sampling': '完整购买时间切片；无随机抽样，保留每个订单全部明细和关联实体',
                  'data_seed': None, 'purchase_interval': [START, END], 'interval_semantics': '左闭右开，原始本地时间',
                  'source_rows': {name: len(rows) for name, rows in raw.items()},
                  'sample_rows': {name: len(rows) for name, rows in selected.items()},
                  'sample_status_counts': dict(Counter(row['order_status'] for row in orders)),
                  'orders_without_items': len(ids - item_orders),
                  'delivered_orders_without_items': sum(row['order_status'] == 'delivered' and row['order_id'] not in item_orders for row in orders),
                  'blank_product_categories': sum(not row['product_category_name'] for row in selected['products'])}
    (output / 'provenance.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding='utf-8')
    return normalized, provenance


def upload_official(benchmark: Benchmark, sample: Path, label: str, status: str, counts: dict) -> dict:
    """上传保留官方列名的四张样本 CSV，检查自动字段映射、队列导入和最终行数。"""
    start = time.monotonic()
    files = [('files', (f'olist_{table}_dataset.csv', (sample / f'olist_{table}_dataset.csv').read_bytes(), 'text/csv')) for table in FIELDS]
    response = benchmark.client.post('/api/datasets', data={'name': label}, files=files)
    response.raise_for_status()
    dataset = response.json()
    uploaded = time.monotonic()
    preview = benchmark.client.get('/api/datasets/' + dataset['id'] + '/preview')
    preview.raise_for_status()
    mapping = {table: entry['mapping'] for table, entry in preview.json().items()}
    benchmark.check(label + '官方列自动映射', all(set(mapping[table]) == set(FIELDS[table]) for table in FIELDS))
    benchmark.client.post('/api/datasets/' + dataset['id'] + '/import', json={'mapping': mapping, 'valid_status': status, 'currency': 'BRL'}).raise_for_status()
    confirmed = time.monotonic()
    ready = benchmark.wait('/api/datasets/' + dataset['id'])
    benchmark.result['imports'].append({'id': dataset['id'], 'label': label, 'status': ready['status'], 'error': ready['error'],
                                       'rows': counts, 'bytes': sum(len(file[1][1]) for file in files),
                                       'upload_seconds': round(uploaded - start, 3),
                                       'queue_import_seconds': round(time.monotonic() - confirmed, 3),
                                       'total_seconds': round(time.monotonic() - start, 3)})
    benchmark.check(label + '数据库行数完整', ready['status'] == 'ready' and ready['summary'] == counts)
    if ready['status'] != 'ready':
        raise RuntimeError('真实数据导入失败：' + str(ready['error']))
    print(f"导入 {label}: {ready['status']}，{time.monotonic() - start:.3f}秒", flush=True)
    return ready


def real_reference(tables: dict, status: str = 'delivered') -> dict:
    """直接关联原始 CSV 并用 Decimal 求和，生成独立答案，将空品类显示为未分类。"""
    result = reference_answers(tables, status)
    for row in result['category']:
        if not row['category']:
            row['category'] = '未分类'
    return result


def main():
    """执行首轮题、重复题和两个并发任务，分别保存结果，不把复测成功加入首轮准确率。"""
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--url', default='http://127.0.0.1:8001')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    tables, provenance = prepare_sample(args.archive, args.output)
    benchmark = Benchmark(args.url, args.output, 'llm')
    benchmark.result['environment'] = {**environment_record(), 'data_source': SOURCE, 'data_seed': None}
    benchmark.result['provenance'] = provenance
    benchmark.check('健康检查', benchmark.client.get('/api/health').json().get('database'))
    benchmark.check('未登录数据访问拒绝', benchmark.client.get('/api/datasets').status_code == 401)
    benchmark.register()
    dataset = upload_official(benchmark, args.output / 'sample', 'Olist真实样本 · 2017年10—11月', 'delivered', provenance['sample_rows'])
    canceled = upload_official(benchmark, args.output / 'sample', 'Olist真实样本 · 有效状态canceled', 'canceled', provenance['sample_rows'])
    benchmark.result['datasets'] = {'main': dataset['id'], 'canceled': canceled['id']}
    reference = real_reference(tables)
    (args.output / 'reference.json').write_text(json.dumps(reference, ensure_ascii=False, indent=2, default=encode_json), encoding='utf-8')
    cases = json.loads(Path('evaluations/cases/end_to_end.json').read_text(encoding='utf-8'))[:9]
    cases[2]['question'] += '空品类使用COALESCE显示为“未分类”，保留全部品类。'
    cases[7]['question'] = cases[7]['question'].replace('2018年1月', '2019年1月')
    natural = [
        {'id': 'natural_overall', 'reference': 'summary', 'question': '这份真实样本整体卖得怎么样？有效订单有多少单，商品销售额和运费分别是多少？给我报告。'},
        {'id': 'natural_monthly', 'reference': 'monthly', 'question': '这两个月的有效订单销售额和订单量怎么样？按购买月份画销售额折线图，给我看看走势。', 'required_tools': ['render_chart']},
        {'id': 'natural_regions', 'reference': 'region', 'question': '有效订单来自哪些州？每个州的订单量和商品销售额是多少？列出全部州并生成报告。'},
    ]
    aliases = {**ALIASES, 'orders': 'order_count', 'orders_num': 'order_count', 'total_order_count': 'order_count',
               'sales': 'item_amount', 'sales_total': 'item_amount', 'amount': 'item_amount'}
    cases += [{**case, 'column_aliases': aliases} for case in natural]
    for case in cases:
        record = benchmark.run_case(case, dataset['id'], reference)
        if case['reference'] == 'monthly':
            checks = check_monthly_supplement(args.output, record, reference)
            record['supplemental_checks'] = checks
            record['passed'] = record['passed'] and all(check['passed'] for check in checks)
        benchmark.checkpoint()
    canceled_case = {'id': 'canceled_summary', 'reference': 'summary',
                     'question': '按本数据集已确认的有效状态canceled统计去重订单量、商品金额和运费。必须包含没有商品明细的订单，金额与运费无记录按0。列名order_count、item_amount、freight_amount，生成报告。'}
    benchmark.run_case(canceled_case, canceled['id'], real_reference(tables, 'canceled'))
    benchmark.run_case({**cases[0], 'id': 'summary_repeat'}, dataset['id'], reference, 'repeat')
    benchmark.run_case({**cases[3], 'id': 'change_repeat'}, dataset['id'], reference, 'repeat')
    pending = []
    for index in range(2):
        case = {**cases[0], 'id': f'concurrent_{index + 1}'}
        start = time.monotonic()
        response = benchmark.client.post('/api/analysis-tasks', json={'dataset_id': dataset['id'], 'question': case['question'], 'mode': 'llm'})
        response.raise_for_status()
        pending.append((case, response.json()['id'], start))
    for case, task_id, start in pending:
        benchmark.run_case(case, dataset['id'], reference, 'concurrent', task_id, start)
    benchmark.measure_api(dataset['id'])
    benchmark.finish_summary()
    benchmark.client.close()
    print(json.dumps(benchmark.result['summary'], ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
