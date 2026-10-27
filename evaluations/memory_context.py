"""通过真实服务检查个性化和多轮摘要，包括 API、队列、Milvus 与模型接口，不保存凭据。"""
import argparse
import asyncio
import json
import secrets
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
import httpx
from backend.core.config import get_settings
from backend.core.storage import read_table
from backend.context.manager import prepare_context
from backend.context.summary import estimate_tokens
from evaluations.benchmark import environment_record


def wait_for(client, path, condition, seconds=240):
    """在限定时间内轮询服务。超时后保留已有结果并停止，不把等待超时标记为成功。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        response = client.get(path); response.raise_for_status(); result = response.json()
        if condition(result):
            return result
        time.sleep(.4)
    raise TimeoutError('有限等待超时：' + path)


def terminal(body):
    """判断任务是否已结束或等待补充输入。任务是否成功由其他检查判断。"""
    return body['status'] not in ('queued','running')


def indexed(body):
    """确认记忆后台索引完成后再检查召回，避免把尚未同步的向量当成匹配失败。"""
    rows = body['preferences'] + body['patterns']
    return bool(rows) and all(row['sync_status'] == 'indexed' for row in rows)


async def compression_probe():
    """调用真实摘要模型压缩较长历史，检查本次问题和原文是否保留，以及请求长度是否符合限制。"""
    settings = get_settings(); original_window = settings.model_context_tokens
    settings.model_context_tokens = 6000
    question = '重新查询本次十一月金额，不能复用旧结果'
    state = {'mode':'llm','question':question,'messages':[{'role':'system','content':'所有数值必须来自本次工具执行。'}], 'artifacts':[]}
    for index in range(4):
        state['messages'] += [{'role':'assistant','content':('历史意图：比较十月和十一月商品金额；已有 table_1 引用，历史数字必须重新查询。' * 45),
                             'tool_calls':[{'id':str(index),'type':'function','function':{'name':'execute_sql','arguments':'{}'}}]},
                            {'role':'tool','tool_call_id':str(index),'content':'历史工具结果：仅用于保留分析意图，不是本次依据。' * 40}]
    state['messages'].append({'role':'user','content':question}); original_count = len(state['messages'])
    started = time.monotonic()
    try:
        result = await prepare_context(state, [])
        budget = 6000 - settings.model_max_tokens - estimate_tokens([]) - 500
        return {'passed':bool(result.get('summary_cursor',0) > 1 and not result.get('summary_error')
                        and estimate_tokens(result['request_messages']) <= budget
                        and len(state['messages']) == original_count
                        and any(item.get('content') == question for item in result['request_messages'])),
                'before_estimated_tokens':estimate_tokens(state['messages']),
                'after_estimated_tokens':estimate_tokens(result['request_messages']), 'budget':budget,
                'summary_cursor':result.get('summary_cursor'), 'summary_error':result.get('summary_error'),
                'summary_usage':result.get('summary_usage'), 'seconds':round(time.monotonic()-started,3)}
    finally:
        settings.model_context_tokens = original_window


def main():
    """创建独立账号和模拟数据，执行五轮模型分析及摘要检查，并保存过程证据。"""
    parser = argparse.ArgumentParser(); parser.add_argument('--url',default='http://127.0.0.1:8001')
    parser.add_argument('--output',default='evaluations/results/memory-context-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    args = parser.parse_args(); output = Path(args.output); output.mkdir(parents=True,exist_ok=False)
    evidence = {'created_at':datetime.now(timezone.utc).isoformat(), 'environment':environment_record(), 'checks':[], 'tasks':[]}
    def save():
        """保存不含凭据的评测证据。保存文件本身不算一项功能验证通过。"""
        (output/'results.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    def check(name, passed, detail=None):
        """执行功能检查后保存结果，方便失败时定位对应阶段。"""
        evidence['checks'].append({'name':name,'passed':bool(passed),'detail':detail})
        save()
        print(name, 'PASS' if passed else 'FAIL', flush=True)
    with httpx.Client(base_url=args.url,timeout=120) as client:
        response = client.post('/api/auth/register',json={'username':'memory_eval_' + uuid4().hex[:12], 'password':secrets.token_urlsafe(24)})
        response.raise_for_status(); client.headers['Authorization'] = 'Bearer ' + response.json()['token']
        evidence['user'] = client.get('/api/auth/me').json()
        response = client.post('/api/datasets/demo'); response.raise_for_status(); dataset = response.json()['id']
        ready = wait_for(client,'/api/datasets/' + dataset,terminal_dataset)
        check('模拟数据真实导入',ready['status'] == 'ready')
        response = client.put('/api/memories/preferences',json={'chart_kind':'line','report_style':'concise'}); response.raise_for_status()
        wait_for(client,'/api/memories',indexed,60); check('偏好后台真实向量索引',True)
        questions = [('统计2017-10的有效订单数和商品金额，按月绘图','3660','line'),
                     ('统计2017-11的有效订单数和商品金额，按月绘图','2890','line'),
                     ('那十月呢？沿用刚才的指标口径，本次画柱状图。','3660','bar'),
                     ('统计2017-11的有效订单数和商品金额，按月绘图','2890','line'),
                     ('那十月呢？请用折线图。','3660','line')]
        conversation = None
        for index, (question, amount, kind) in enumerate(questions,1):
            started = time.monotonic()
            response = client.post('/api/analysis-tasks',json={'dataset_id':dataset,'conversation_id':conversation,'question':question,'mode':'llm'})
            response.raise_for_status(); task = response.json(); conversation = task['conversation_id']
            task = wait_for(client,'/api/analysis-tasks/' + task['id'],terminal)
            check('第%d轮交付' % index,task['status'] == 'succeeded',task.get('error'))
            bodies = [read_table(artifact['id'], task['id']) for artifact in task['artifacts'] if artifact['kind'] == 'table']
            rows = [row for body in bodies for row in body['rows']]
            amounts = [Decimal(str(value)) for row in rows for key,value in row.items() if value is not None and any(word in key.lower() for word in ('amount','sales','revenue','price'))]
            charts = [a for a in task['artifacts'] if a['kind'] == 'chart']
            check('第%d轮独立金额基准' % index,Decimal(amount) in amounts,{'expected':amount,'rows':rows})
            numeric = any(len(body['rows']) == 1 and
                          any(Decimal(str(value)) == Decimal(amount) for key, value in body['rows'][0].items()
                              if value is not None and any(word in key.lower() for word in ('amount','sales','revenue','price'))) and
                          any(Decimal(str(value)) == 33 for key, value in body['rows'][0].items()
                              if value is not None and any(word in key.lower() for word in ('order_count','order_num','orders','订单')))
                          for body in bodies)
            check('第%d轮金额与去重订单数共同核对' % index,numeric,{'expected_amount':amount,'expected_order_count':33})
            check('第%d轮图表偏好或本次覆盖' % index,bool(charts) and all(a['chart_kind'] == kind for a in charts))
            evidence['tasks'].append({'task':task,'seconds':round(time.monotonic()-started,3)})
            save()
            wait_for(client,'/api/memories',indexed,60)
        history = client.get('/api/conversations/' + conversation).json(); evidence['conversation'] = history
        check('跨任务真实摘要及完整原文',bool(history['summary']) and history['summary_cursor'] >= 2 and len(history['messages']) == 10, {'cursor':history['summary_cursor'],'messages':len(history['messages'])})
        memories = client.get('/api/memories').json(); evidence['memories'] = memories
        check('常问模式按独立成功任务累计',any(row['use_count'] >= 3 for row in memories['patterns']))
        recall = client.get('/api/memories/suggestions',params={'question':'统计2018-01的有效订单数和商品金额，按月绘图'}).json()
        check('真实嵌入与Milvus语义召回',recall.get('vector_status') == 'searched' and bool(recall['patterns']),recall)
        preference = next(row for row in memories['preferences'] if row['key'] == 'chart_kind')
        response = client.delete('/api/memories/preference/' + preference['id']); response.raise_for_status()
        after = client.get('/api/memories/suggestions',params={'question':'金额趋势'}).json()
        check('忘记偏好立即停止使用','chart_kind' not in after['preferences'])
        check('未填写模板参数被拒绝',client.post('/api/analysis-tasks',json={'dataset_id':dataset,'question':'统计[时间范围]的有效订单数','mode':'llm'}).status_code == 422)
        compression = asyncio.run(compression_probe()); evidence['compression'] = compression
        check('真实任务内摘要保持预算',compression['passed'],compression)
    print('通过 %d/%d；取证目录：%s' % (sum(item['passed'] for item in evidence['checks']),len(evidence['checks']),output))


def terminal_dataset(body):
    """判断模拟数据导入是否结束，保留接口返回的失败原因。"""
    return body['status'] not in ('uploaded','importing')


if __name__ == '__main__':
    main()
