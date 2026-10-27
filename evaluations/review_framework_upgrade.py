"""用原始样本独立复核框架迁移评测，保留原始评分，只另存复核结果。"""
import argparse
import csv
import hashlib
import json
import statistics
from pathlib import Path
from backend.ingestion.mapping import default_mapping
from evaluations.olist_real import real_reference
from evaluations.benchmark import normalize_columns, compare_candidate, candidates_correct, matching_grain, percentile
from evaluations.retest_natural import check_monthly_supplement


def review(output: Path) -> dict:
    """校验样本哈希、重算所有口径，分别核对完整表与补充子集、派生指标和交付状态。"""
    raw_path = output/'results.json'
    before = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    raw = json.loads(raw_path.read_text(encoding='utf-8'))
    provenance = json.loads((output/'provenance.json').read_text(encoding='utf-8'))
    tables = {}
    for table in ('customers','products','orders','order_items'):
        path = output/'sample'/f'olist_{table}_dataset.csv'
        assert hashlib.sha256(path.read_bytes()).hexdigest() == provenance['sample_csv_sha256'][table]
        with path.open(encoding='utf-8-sig',newline='') as stream:
            rows = list(csv.DictReader(stream))
        mapping = default_mapping(list(rows[0]),table)
        tables[table] = [{field:row[column] for field,column in mapping.items()} for row in rows]
    references = {status:real_reference(tables,status) for status in ('delivered','canceled')}
    keys = {case['id']:case['reference'] for case in json.loads(Path('evaluations/cases/end_to_end.json').read_text(encoding='utf-8'))}
    keys.update({'natural_overall':'summary','natural_monthly':'monthly','natural_regions':'region',
                 'summary_repeat':'summary','change_repeat':'change','concurrent_1':'summary','concurrent_2':'summary',
                 'canceled_summary':'summary','monthly_after_fix':'monthly','natural_monthly_after_fix':'monthly',
                 'region_after_fix':'region','natural_overall_after_fix':'summary','batch_after_fix':'summary'})
    results=[]
    for case in raw['cases']:
        identifier=case['id']
        task=json.loads((output/identifier/'task.json').read_text(encoding='utf-8'))
        status='canceled' if identifier=='canceled_summary' else 'delivered'
        reference=references[status]
        expected=reference[keys[identifier]]
        candidates=[]
        for artifact in task['artifacts']:
            if artifact['kind']!='table' or artifact.get('delivery_role')=='intermediate':
                continue
            body=json.loads((output/identifier/(artifact['id']+'.json')).read_text(encoding='utf-8'))
            existing=next((item for item in case.get('table_checks',[]) if item['artifact_id']==artifact['id']),{})
            aliases=existing.get('column_aliases',{})
            if matching_grain(body['columns'],aliases,expected):
                normalized=[normalize_columns(row,aliases) for row in body['rows']]
                candidates.append({'artifact_id':artifact['id'], **compare_candidate(normalized,expected)})
        supplemental=[]
        if keys[identifier]=='monthly':
            final_ids={item['id'] for item in task['artifacts'] if item.get('delivery_role')!='intermediate'}
            checked_case={**case,'downloads':[item for item in case.get('downloads',[]) if item['id'] in final_ids]}
            supplemental=check_monthly_supplement(output,checked_case,reference)
        numeric=candidates_correct(candidates)
        output_requirements = not ('不要绘图' in case['question'] and any(item['kind']=='chart' for item in task['artifacts']))
        passed=numeric and output_requirements and all(item['passed'] for item in supplemental) and all(case.get(key,False) for key in ('delivery_passed','required_tools_passed','clarification_passed','sse_replay_passed')) and case['status']=='succeeded'
        results.append({'id':identifier,'phase':case['phase'],'raw_passed':case['passed'],'numeric_passed':numeric,
                        'passed':passed,'output_requirements_passed':output_requirements,'table_checks':candidates,'supplemental_checks':supplemental,'seconds':case['seconds']})
    single=[case for case in results if case['phase']=='single'] or results
    summary={'count':len(single),'correct_complete':sum(case['passed'] for case in single),
             'correct_complete_percent':round(100*sum(case['passed'] for case in single)/len(single),1),
             'numeric_correct':sum(case['numeric_passed'] for case in single),
             'median_seconds':round(statistics.median(case['seconds'] for case in single),3),
             'p95_seconds':percentile([case['seconds'] for case in single],.95)}
    assert before==hashlib.sha256(raw_path.read_bytes()).hexdigest()
    result={'raw_results_sha256':before,'sample_sha256_verified':True,'scope':'独立复核，不覆盖原始评分；首轮与定向复测分别统计',
            'summary':summary,'cases':results}
    (output/'independent-review.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def main():
    """接收已有证据目录并打印精简复核摘要，不重新调用模型。"""
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(review(args.output)['summary'],ensure_ascii=False))


if __name__=='__main__':
    main()
