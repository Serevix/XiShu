"""使用不同随机种子的合成数据检查数值正确性，包括多商品订单和跨记录客户合并。"""
import argparse
import json
import random
from pathlib import Path
from evaluations.benchmark import Benchmark, environment_record
from evaluations.oracle import reference_answers
from backend.ingestion.mapping import FIELDS


def varied_tables(seed: int, count: int) -> dict:
    """生成金额和分布不同的合成数据，并让同一客户出现在多条记录中，避免只验证固定示例答案。"""
    rng = random.Random(seed)
    tables = {name: [] for name in FIELDS}
    categories = ['books', 'electronics', 'home', 'toys', 'sport']
    tables['products'] = [{'product_id': f'p-{index}', 'category_name': category}
                          for index, category in enumerate(categories)]
    for index in range(60):
        tables['customers'].append({'customer_id': f'c-{index}', 'customer_unique_id': f'u-{index % 41}',
                                    'city': 'test_city', 'state': rng.choice(['SP', 'RJ', 'MG', 'BA'])})
    for index in range(count):
        order_id = f'o-{index}'
        month, day = rng.choice([10, 11]), rng.randint(1, 28)
        tables['orders'].append({'order_id': order_id, 'customer_id': f'c-{rng.randrange(60)}',
                                'status': rng.choices(['delivered', 'cancelled', 'unavailable'], [75, 15, 10])[0],
                                'purchase_time': f'2017-{month}-{day:02} 09:00:00',
                                'delivered_time': '', 'estimated_delivery_time': ''})
        for item in range(1, rng.randint(1, 4) + 1):
            tables['order_items'].append({'order_id': order_id, 'order_item_id': item,
                                         'product_id': f'p-{rng.randrange(5)}',
                                         'price': f'{rng.randrange(0, 90001) / 100:.2f}',
                                         'freight_value': f'{rng.randrange(0, 3001) / 100:.2f}'})
    return tables


def main():
    """每种新数据首次执行六道题，保留失败结果，不通过反复重试筛选成功来计算准确率。"""
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8001')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    benchmark = Benchmark(args.url, args.output, 'llm')
    benchmark.result['environment'] = {**environment_record(), 'data_seed': [17, 83, 209],
                                       'data_source': 'new seeded synthetic distributions'}
    benchmark.register()
    cases = json.loads(Path('evaluations/cases/end_to_end.json').read_text(encoding='utf-8'))
    chosen = [case for case in cases if case['id'] in
              ('summary', 'monthly', 'category_python', 'monthly_change', 'aov_freight', 'repeat_customer')]
    assert len(chosen) == 6
    for seed, count in ((17, 137), (83, 251), (209, 503)):
        tables = varied_tables(seed, count)
        dataset = benchmark.upload(f'新分布seed{seed}-{count}订单', tables)
        if dataset['status'] != 'ready':
            raise RuntimeError('独立分布导入失败')
        reference = reference_answers(tables)
        for case in chosen:
            benchmark.run_case({**case, 'id': f"seed{seed}_{case['id']}"}, dataset['id'], reference)
    benchmark.finish_summary()
    benchmark.client.close()
    print(json.dumps(benchmark.result['summary'], ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
