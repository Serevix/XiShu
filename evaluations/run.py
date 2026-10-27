"""通过真实 API 执行固定模拟题，记录结果正确性和从提交到完成的耗时。"""
import argparse
import getpass
import json
import time
from decimal import Decimal
from pathlib import Path
import httpx


def wait_status(client, path: str, field: str, pending: set[str], timeout: int = 360) -> dict:
    """在限定时间内等待任务。超过时限时明确报错，不把等待结束当作任务成功。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(path)
        response.raise_for_status()
        result = response.json()
        if result[field] not in pending:
            return result
        time.sleep(1)
    raise TimeoutError('任务等待超时')


def main() -> None:
    """用项目账号创建模拟数据并运行题目，再与独立计算的标准答案比较。"""
    parser = argparse.ArgumentParser()
    parser.add_argument('--username', required=True)
    parser.add_argument('--url', default='http://127.0.0.1:8001')
    parser.add_argument('--mode', choices=['demo','llm'], default='demo')
    arguments = parser.parse_args()
    password = getpass.getpass('项目账号密码（不回显）：')
    root = Path(__file__).resolve().parent
    cases = json.loads((root / 'cases/demo.json').read_text(encoding='utf-8'))
    results = []
    with httpx.Client(base_url=arguments.url, timeout=120) as client:
        response = client.post('/api/auth/login', json={'username':arguments.username,'password':password})
        response.raise_for_status()
        client.headers['Authorization'] = 'Bearer ' + response.json()['token']
        response = client.post('/api/datasets/demo')
        response.raise_for_status()
        dataset = response.json()['id']
        ready = wait_status(client, '/api/datasets/' + dataset, 'status', {'uploaded','importing'})
        if ready['status'] != 'ready':
            raise RuntimeError('评测数据导入失败')
        for case in cases:
            start = time.monotonic()
            response = client.post('/api/analysis-tasks', json={'dataset_id':dataset,'question':case['question'],'mode':arguments.mode})
            response.raise_for_status()
            task_id = response.json()['id']
            task = wait_status(client, '/api/analysis-tasks/' + task_id, 'status', {'queued','running'})
            passed = False
            for artifact in task['artifacts']:
                if artifact.get('sql') and {'order_count','item_amount'} <= set(artifact['columns']):
                    response = client.get('/api/artifacts/' + artifact['id'])
                    response.raise_for_status()
                    rows = response.json()['rows']
                    if rows and len(rows) == 1:
                        row = rows[0]
                        passed = row['order_count'] == case['expected']['order_count'] and Decimal(str(row['item_amount'])) == Decimal(case['expected']['item_amount'])
                        break
            results.append({'question':case['question'],'task_id':task_id,'mode':arguments.mode,
                            'status':task['status'],'passed':passed and task['status']=='succeeded',
                            'seconds':round(time.monotonic()-start,2),'usage':task['usage']})
    output = root / 'results' / f'evaluation-{int(time.time())}.json'
    output.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f"完成：{sum(item['passed'] for item in results)}/{len(results)}；模式={arguments.mode}；报告={output}")


if __name__ == '__main__':
    main()
