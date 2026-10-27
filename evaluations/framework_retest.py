"""针对框架升级发现的问题复测，原首轮证据不覆盖，使用同一真实样本和独立答案。"""
import argparse
import json
from pathlib import Path
from evaluations.benchmark import Benchmark, environment_record
from evaluations.olist_real import prepare_sample, real_reference, upload_official, SOURCE
from evaluations.natural_questions import ALIASES
from evaluations.retest_natural import check_monthly_supplement
from evaluations.review_framework_upgrade import review


def main():
    """用新账号导入相同切片，复测月度精度、地区完整性、金额别名及真实多工具调用。"""
    parser=argparse.ArgumentParser()
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--url',default='http://127.0.0.1:8001')
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    tables,provenance=prepare_sample(args.archive,args.output)
    benchmark=Benchmark(args.url,args.output,'llm')
    benchmark.result['environment']={**environment_record(),'data_source':SOURCE,'data_seed':None}
    benchmark.result['provenance']=provenance
    benchmark.result['scope']='框架升级修复后五项定向复测，不代替首轮十三题'
    benchmark.register()
    dataset=upload_official(benchmark,args.output/'sample','框架升级修复后同样本','delivered',provenance['sample_rows'])
    benchmark.result['datasets']={'main':dataset['id']}
    base=json.loads(Path('evaluations/cases/end_to_end.json').read_text(encoding='utf-8'))
    cases=[{**base[1],'id':'monthly_after_fix'},
           {'id':'natural_monthly_after_fix','reference':'monthly','question':'这两个月的有效订单销售额和订单量怎么样？按购买月份画销售额折线图，给我看看走势。','required_tools':['render_chart'],'column_aliases':ALIASES},
           {**base[5],'id':'region_after_fix'},
           {'id':'natural_overall_after_fix','reference':'summary','question':'这份真实样本整体卖得怎么样？有效订单有多少单，商品销售额和运费分别是多少？给我报告。','column_aliases':ALIASES},
           {'id':'batch_after_fix','reference':'summary','question':'请在同一轮分别调用 sales_metrics 和 customer_repeat_metrics，汇总有效订单量、商品销售额、商品项运费以及独立客户复购率，不要绘图，生成报告。','required_tools':['sales_metrics','customer_repeat_metrics'],'forbidden_tools':['render_chart']}]
    reference=real_reference(tables)
    for case in cases:
        record=benchmark.run_case(case,dataset['id'],reference,'after_fix')
        if case['reference']=='monthly':
            record['supplemental_checks']=check_monthly_supplement(args.output,record,reference)
            record['passed']=record['passed'] and all(check['passed'] for check in record['supplemental_checks'])
        benchmark.checkpoint()
    benchmark.finish_summary()
    benchmark.client.close()
    print(json.dumps(review(args.output)['summary'],ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
