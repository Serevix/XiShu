"""通过项目 API 上传本地 Olist 文件，仍经过登录、归属检查和导入校验。"""
import argparse
import getpass
from contextlib import ExitStack
from pathlib import Path
import httpx


def main() -> None:
    """登录项目账号，上传已下载的四个 Olist CSV，并确认默认字段映射。"""
    parser = argparse.ArgumentParser(description='通过 API 导入本地 Olist 数据')
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--username', required=True)
    parser.add_argument('--url', default='http://127.0.0.1:8001')
    arguments = parser.parse_args()
    password = getpass.getpass('项目账号密码（不回显）：')
    with httpx.Client(base_url=arguments.url, timeout=120) as client:
        response = client.post('/api/auth/login', json={'username':arguments.username, 'password':password})
        response.raise_for_status()
        client.headers['Authorization'] = 'Bearer ' + response.json()['token']
        with ExitStack() as stack:
            files = []
            for table in ('customers', 'products', 'orders', 'order_items'):
                path = arguments.directory / f'olist_{table}_dataset.csv'
                files.append(('files', (path.name, stack.enter_context(path.open('rb')), 'text/csv')))
            response = client.post('/api/datasets', data={'name':'Olist 历史电商数据'}, files=files)
            response.raise_for_status()
        dataset = response.json()['id']
        response = client.get(f'/api/datasets/{dataset}/preview')
        response.raise_for_status()
        mapping = {table:body['mapping'] for table, body in response.json().items()}
        response = client.post(f'/api/datasets/{dataset}/import', json={'mapping':mapping, 'currency':'BRL'})
        response.raise_for_status()
        print(f'导入任务已提交，数据集 ID：{dataset}；请在页面查看结果。')


if __name__ == '__main__':
    main()
