"""使用日常中文问题评测，不在问题中指定输出列名，并预先确定列名对应规则。"""
import argparse
import json
import time
from pathlib import Path
from evaluations.benchmark import Benchmark, environment_record
from evaluations.generalization import varied_tables
from evaluations.oracle import reference_answers

ALIASES = {
    'total_orders': 'order_count', 'orders_count': 'order_count', 'order_num': 'order_count',
    '订单数': 'order_count', '订单量': 'order_count', '去重订单量': 'order_count',
    'total_sales': 'item_amount', 'sales_amount': 'item_amount', 'total_amount': 'item_amount',
    'total_price': 'item_amount', '商品销售额': 'item_amount', '商品金额': 'item_amount', '销售额': 'item_amount',
    'total_freight': 'freight_amount', 'freight': 'freight_amount', '运费': 'freight_amount',
    'purchase_month': 'month', '月份': 'month', 'category_name': 'category', '品类': 'category',
    'sales_share_percent': 'share_percent', 'sales_percentage': 'share_percent', '占比': 'share_percent',
    '占比百分比': 'share_percent', 'sales_share': 'share_percent', 'percentage': 'share_percent',
    'customer_state': 'state', '州': 'state',
}


def main():
    """使用新账号和数据运行六道自然语言题。列名无法匹配时保留证据，交给后续复核。"""
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8001')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    benchmark = Benchmark(args.url, args.output, 'llm')
    benchmark.result['environment'] = {**environment_record(), 'data_seed': 321,
                                       'data_source': 'new synthetic distribution, natural questions'}
    benchmark.register()
    tables = varied_tables(321, 193)
    dataset = benchmark.upload('自然中文193订单', tables)
    if dataset['status'] != 'ready':
        raise RuntimeError('自然问题数据导入失败')
    questions = [
        ('natural_overall', 'summary', '这份数据整体卖得怎么样？帮我算一下有效订单有多少单、商品销售额和运费分别是多少，给一份报告。'),
        ('natural_monthly', 'monthly', '每个月的有效订单销售额和订单量怎么样？按月份画一张销售额折线图，给我看看走势。'),
        ('natural_categories', 'category', '有效订单里，各品类卖了多少钱、有多少订单、销售额占比百分比是多少？全部列出来并画柱状图。'),
        ('natural_regions', 'region', '有效订单来自哪些州？各州订单量和商品销售额是多少，列出全部州。'),
        ('natural_repeat', 'customer', '真正独立的客户有多少？有效订单中买过至少两单的客户有多少，复购率百分比是多少？'),
        ('natural_monthly_reword', 'monthly', '按购买时间把有效订单逐月汇总，给我每个月的商品金额和去重订单数，生成报告。'),
    ]
    reference = reference_answers(tables)
    benchmark.check('纯空白问题接口拒绝', benchmark.client.post('/api/analysis-tasks', json={
        'dataset_id': dataset['id'], 'question': '   ', 'mode': 'llm'}).status_code == 422)
    for identifier, key, question in questions:
        case = {'id': identifier, 'reference': key, 'question': question, 'column_aliases': ALIASES}
        if identifier in ('natural_monthly', 'natural_categories'):
            case['required_tools'] = ['render_chart']
        benchmark.run_case(case, dataset['id'], reference, 'natural')
    clarification = {'id': 'clarified_repeat', 'reference': 'customer',
                     'question': '请先调用澄清工具向我询问分析方向，在我补充之前不要计算。',
                     'resume': '统计有效订单的独立客户数、复购客户数和复购率百分比。按 customer_unique_id 识别客户，结果列名 unique_customers、repeat_customers、repeat_rate，生成报告。',
                     'required_tools': ['customer_repeat_metrics']}
    clarification_started = time.monotonic()
    response = benchmark.client.post('/api/analysis-tasks', json={
        'dataset_id': dataset['id'], 'question': clarification['question'], 'mode': 'llm'})
    response.raise_for_status()
    identifier = response.json()['id']
    waiting = benchmark.wait('/api/analysis-tasks/' + identifier)
    benchmark.check('纯空白澄清接口拒绝', waiting['status'] == 'waiting_input' and
                    benchmark.client.post('/api/analysis-tasks/' + identifier + '/resume',
                                          json={'answer': ' \n\t'}).status_code == 422)
    benchmark.run_case(clarification, dataset['id'], reference, 'clarified', task_id=identifier, start=clarification_started)
    benchmark.finish_summary()
    benchmark.client.close()
    print(json.dumps(benchmark.result['summary'], ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
