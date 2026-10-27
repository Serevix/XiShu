"""生成模拟电商数据，包含取消订单、多商品订单和不同月份的销售变化。"""
import csv
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.ingestion.mapping import FIELDS


def generate_tables(seed: int = 42) -> dict[str, list[dict]]:
    """根据固定随机种子生成四表，并设置电子品类下降、家居品类上涨的变化，供重复验证使用。"""
    rng = random.Random(seed)
    tables = {name: [] for name in FIELDS}
    categories = [('electronics', 180, 90), ('books', 60, 60), ('home', 90, 110)]
    tables['products'] = [{'product_id': f'p-{category}', 'category_name': category} for category, _, _ in categories]
    for month in (10, 11):
        for category, october_price, november_price in categories:
            for index in range(12):
                key = f'{month}-{category}-{index}'
                tables['customers'].append({'customer_id': 'c-' + key, 'customer_unique_id': f'u-{category}-{index}',
                                             'city': rng.choice(['sao_paulo', 'rio_de_janeiro']), 'state': rng.choice(['SP', 'RJ'])})
                tables['orders'].append({'order_id': 'o-' + key, 'customer_id': 'c-' + key,
                                         'status': 'cancelled' if index == 0 else 'delivered',
                                         'purchase_time': f'2017-{month:02}-{index + 1:02} 10:00:00',
                                         'delivered_time': '' if index == 0 else f'2017-{month:02}-{index + 4:02} 10:00:00',
                                         'estimated_delivery_time': f'2017-{month:02}-{index + 6:02} 10:00:00'})
                tables['order_items'].append({'order_id': 'o-' + key, 'order_item_id': 1, 'product_id': 'p-' + category,
                                              'price': october_price if month == 10 else november_price, 'freight_value': 10})
                if index % 4 == 0:
                    tables['order_items'].append({'order_id': 'o-' + key, 'order_item_id': 2, 'product_id': 'p-books',
                                                  'price': 5, 'freight_value': 2})
    return tables


def write_demo_files(directory: Path) -> dict:
    """把四张模拟表写成模板 CSV，并返回生成的文件信息。"""
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for table, rows in generate_tables().items():
        path = directory / f'{table}.csv'
        with path.open('w', newline='', encoding='utf-8-sig') as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS[table])
            writer.writeheader()
            writer.writerows(rows)
        manifest[table] = {'path': str(path), 'name': path.name, 'sheet': None}
    return manifest


if __name__ == '__main__':
    directory = Path('storage/uploads/demo-template')
    write_demo_files(directory)
    print(f'模拟数据已生成：{directory.resolve()}')
