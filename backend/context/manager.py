"""从完整消息中整理模型请求，在控制长度的同时保留工具调用与结果的对应关系。"""
import json
from backend.core.config import get_settings
from backend.context.summary import estimate_tokens, summarize


def safe_cut(messages: list[dict], recent: int) -> int:
    """确定历史消息的切分位置。保留下来的工具结果必须同时保留对应的模型调用消息。"""
    if len(messages) <= 1:
        return len(messages)
    cut = max(1, len(messages) - recent)
    # 近期消息不能从 tool 结果开始，需要向前保留对应的 assistant 调用，否则模型请求会缺少配对信息。
    while cut > 1 and messages[cut].get('role') == 'tool':
        cut -= 1
    return cut


def task_digest(artifacts: list[dict]) -> str:
    """整理已有结果的引用信息和少量预览，让模型仍能使用真实文件 ID，不必自行编造。"""
    records = []
    for item in artifacts:
        record = {key: item[key] for key in ('id','artifact_ref','kind','columns','row_count','source_id','metric_id','metric_scope','source_row_count','plotted_row_count') if key in item}
        if item['kind'] == 'table':
            record['preview'] = item.get('preview', [])[:2]
        records.append(record)
    return json.dumps(records, ensure_ascii=False)


def compressed_request(state: dict, summary: str, cut: int) -> list[dict]:
    """整理压缩后的模型请求，保留系统规则、本次问题、摘要、结果引用和近期工具消息，不删除原文。"""
    messages = state['messages']
    root = [messages[0]] if messages and messages[0]['role'] == 'system' else []
    # 只缩减发给模型的历史内容，保留系统规则、本次完整问题和结果引用，不删除 state 中的原始消息。
    root += [{'role':'system','content':'以下是历史摘要及当前工具事实引用，仅作背景；历史数值不能作为当前回答依据。\n' + summary + '\n' + task_digest(state.get('artifacts', []))},
             {'role':'user','content':state['question']}]
    if state.get('clarification_answer'):
        root.append({'role':'user','content':'本次澄清：' + state['clarification_answer']})
    # 本次问题已经完整加入请求，所以近期消息中的相同问题不再重复加入。
    tail = [item for item in messages[cut:] if not (item.get('role') == 'user' and item.get('content') == state['question'])]
    return [*root, *tail]


async def prepare_context(state: dict, tools: list[dict]) -> dict:
    """请求接近长度上限时总结旧消息。只有摘要成功才更新进度；失败时保留旧进度并使用较少的历史内容。"""
    settings = get_settings()
    if state['mode'] != 'llm':
        return {'request_messages': state['messages']}
    # 先为工具说明、最大输出长度和消息格式留出空间，余下的长度才用于输入消息。
    budget = settings.model_context_tokens - settings.model_max_tokens - estimate_tokens(tools) - 500
    messages = state['messages']
    previous = state.get('message_summary', '')
    cursor = state.get('summary_cursor', 1)
    request = compressed_request(state, previous, cursor) if previous else messages
    if estimate_tokens(request) <= budget * settings.context_summary_trigger:
        return {'request_messages': request}
    # 这里总结单次分析中的工具消息。conversations.py 另外负责总结跨分析任务的会话历史。
    cut = safe_cut(messages, settings.context_recent_messages)
    update = {}
    if cut > cursor:
        try:
            summary, usage = await summarize(previous, messages[cursor:cut])
            # 只有摘要调用成功才返回新的摘要和进度，避免把处理失败的消息标记为已经总结。
            update = {'message_summary':summary, 'summary_cursor':cut, 'summary_error': None,
                      'summary_usage': {key:state.get('summary_usage', {}).get(key,0) + usage.get(key,0) for key in usage}}
            request = compressed_request(state, summary, cut)
        except Exception as error:
            # 摘要失败时使用旧摘要、近期消息和结果引用控制本轮长度，不更新保存的摘要进度，后续还可以重试。
            update['summary_error'] = '摘要暂不可用：' + type(error).__name__
            request = compressed_request(state, previous, cut)
    # 请求仍然过长时继续减少近期消息，但始终一起保留或移除工具调用及其结果。
    while estimate_tokens(request) > budget and cut < len(messages) - 1:
        cut += 1
        while cut < len(messages) and messages[cut].get('role') == 'tool':
            cut += 1
        request = compressed_request(state, update.get('message_summary', previous), cut)
    if estimate_tokens(request) > budget:
        raise ValueError('系统约束、工具事实与本次问题超过预算；未截断当前问题，请提高窗口或缩小分析范围')
    return {**update, 'request_messages': request}
