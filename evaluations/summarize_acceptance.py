"""汇总本轮验收证据，分别记录首次执行、重复负载和多轮记忆测试。"""
import argparse
import json
from importlib.metadata import version
from pathlib import Path
from evaluations.benchmark import percentile, environment_record, candidates_correct


def metrics(records: list[dict]) -> dict:
    """按各阶段实际任务数统计完成率、数值正确率、交付率和延迟，不把重试成功算成首次正确。"""
    values = [row['seconds'] for row in records]
    return {'count': len(records), 'completed': sum(row['status'] == 'succeeded' for row in records),
            'numeric_correct': sum(bool(row.get('numeric_passed')) for row in records),
            'fully_passed': sum(bool(row['passed']) for row in records),
            'p50_seconds': percentile(values, .5), 'p95_seconds': percentile(values, .95),
            'min_seconds': min(values) if values else None, 'max_seconds': max(values) if values else None,
            'sse_passed': sum(bool(row.get('sse_replay_passed')) for row in records),
            'automatic_repair_events': sum(len(row.get('errors', [])) for row in records)}


def main():
    """读取已有结果和公开环境信息，生成不含凭据的汇总文件。缺少必要阶段时明确报错。"""
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default='20261010')
    args = parser.parse_args()
    evidence_root = Path('evaluations/results')
    groups, all_cases, all_checks, imports = {}, [], [], []
    for name in ('baseline', 'generalization', 'edges', 'natural'):
        result = json.loads((evidence_root / f'acceptance-{name}-{args.date}' / 'results.json').read_text(encoding='utf-8'))
        for row in result['cases']:
            # 对较早的结果重新做严格检查，但不改写原文件。汇总可以指出额外问题，不提高原结论。
            row['numeric_passed'] = candidates_correct(row.get('table_checks', []))
            row['passed'] = row['passed'] and row['numeric_passed']
        groups[name] = metrics(result['cases'])
        all_cases.extend([{**row, 'group': name} for row in result['cases']])
        all_checks.extend(result['checks'])
        imports.extend(result['imports'])
    memory = json.loads((evidence_root / f'acceptance-memory-{args.date}' / 'results.json').read_text(encoding='utf-8'))
    retest = json.loads((evidence_root / f'acceptance-retest-{args.date}' / 'results.json').read_text(encoding='utf-8'))
    window_retest = json.loads((evidence_root / f'acceptance-window-retest-{args.date}' / 'results.json').read_text(encoding='utf-8'))
    numeric_retest = json.loads((evidence_root / f'acceptance-numeric-retest-{args.date}' / 'results.json').read_text(encoding='utf-8'))
    supplemental_review = json.loads((Path('storage/traces') / f'acceptance-{args.date}' / 'supplemental-review.json').read_text(encoding='utf-8'))
    natural_review = json.loads((evidence_root / f'acceptance-natural-{args.date}' / 'alias-review.json').read_text(encoding='utf-8'))
    reviewed = {row['id']: row for row in natural_review['cases']}
    primary = [row for row in all_cases if row['phase'] not in ('repeat', 'concurrent')]
    semantic_primary = [{**row, 'numeric_passed': reviewed[row['id']]['numeric_passed'],
                         'passed': reviewed[row['id']]['semantic_delivery_passed']}
                        if row['group'] == 'natural' else row for row in primary]
    repeated = [row for row in all_cases if row['phase'] in ('repeat', 'concurrent')]
    summary = {'environment_at_completion': environment_record(), 'groups': groups,
               'first_run': metrics(primary), 'repeat_and_concurrent': metrics(repeated),
               'first_run_with_human_alias_review': metrics(semantic_primary),
               'after_fix_retest': metrics(retest['cases']),
               'after_window_fix_retest': metrics(window_retest['cases']),
               'after_numeric_fix_retest': metrics(numeric_retest['cases']),
               'supplemental_review_before_numeric_fix': supplemental_review,
               'natural_review': natural_review,
               'flow_checks': {'passed': sum(row['passed'] for row in all_checks), 'total': len(all_checks)},
               'memory_checks': {'passed': sum(row['passed'] for row in memory['checks']), 'total': len(memory['checks']),
                                 'tasks': len(memory['tasks']), 'p50_seconds': percentile([row['seconds'] for row in memory['tasks']], .5),
                                 'compression': memory['compression']},
               'analysis_tasks_total': len(all_cases) + len(memory['tasks']) + len(retest['cases']) + len(window_retest['cases']) + len(numeric_retest['cases']),
               'failed_cases': [{key: row.get(key) for key in ('group', 'id', 'status', 'error', 'numeric_passed', 'passed')}
                                for row in all_cases if not row['passed']],
               'largest_import': max(imports, key=lambda row: row['rows']['orders']),
               'slowest_first_runs': [{'group': row['group'], 'id': row['id'], 'seconds': row['seconds'], 'timing': row['timing']}
                                     for row in sorted(primary, key=lambda row: row['seconds'], reverse=True)[:3]],
               'packages': {name: version(name) for name in ('fastapi','sqlalchemy','langgraph','celery','pandas','matplotlib','pymilvus','httpx')}}
    target = Path('storage/traces') / f'acceptance-{args.date}' / 'summary.json'
    target.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: summary[key] for key in ('first_run','first_run_with_human_alias_review','after_fix_retest','after_window_fix_retest','repeat_and_concurrent','flow_checks','memory_checks','analysis_tasks_total','failed_cases')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
