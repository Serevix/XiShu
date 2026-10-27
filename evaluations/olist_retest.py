"""修复运费定义、断连重试及导入统计后复测原始问题，首轮失败记录仍保留。"""
import argparse
import json
from pathlib import Path
from evaluations.benchmark import Benchmark, environment_record
from evaluations.olist_real import prepare_sample, real_reference, upload_official, SOURCE


def main():
    """用新账号导入相同真实样本，检查首次查询，并各执行两次运费题和地区题。"""
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
    benchmark.result['scope'] = '修复后的定向复测，不能替代首轮总体成绩'
    benchmark.register()
    dataset = upload_official(benchmark, args.output / 'sample', 'Olist同样本 · 修复后复测', 'delivered', provenance['sample_rows'])
    benchmark.result['datasets'] = {'main': dataset['id']}
    cases = json.loads(Path('evaluations/cases/end_to_end.json').read_text(encoding='utf-8'))
    selected = [{**cases[0], 'id': 'summary_after_import'}]
    for index in (1, 2):
        selected += [{**cases[4], 'id': f'aov_after_fix_{index}'}, {**cases[5], 'id': f'region_after_fix_{index}'}]
    for case in selected:
        benchmark.run_case(case, dataset['id'], real_reference(tables), 'after_fix')
    benchmark.finish_summary()
    benchmark.client.close()
    print(json.dumps(benchmark.result['summary'], ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
