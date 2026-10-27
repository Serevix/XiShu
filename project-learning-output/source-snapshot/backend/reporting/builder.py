"""根据已保存的计算结果生成 Markdown 报告，不让模型重新生成结果表中的数字。"""
from backend.core.storage import read_table, write_artifact


def escape_cell(value) -> str:
    """转义 Markdown 表格中的竖线和换行。空值显示为缺失或无定义，而不是零。"""
    return '无定义/缺失' if value is None else str(value).replace('|', '\\|').replace('\n', ' ')


def build_report(state: dict) -> dict:
    """根据问题、统计口径和实际结果生成报告，并附上表格、图表来源及计算代码。"""
    args = state['action']['arguments']
    style = state.get('personalization', {}).get('report_style')
    notes = args['notes'][:3] if style == 'concise' else args['notes']
    lines = ['# ' + args['title'][:120], '', '## 任务', state['question'], '',
             '## 运行模式', '规则演示（未调用聊天模型）' if state['mode'] == 'demo' else '模型自主工具调用', '',
             '## 指标口径', f"币种：{state['metrics']['currency']}；有效订单状态：{state['metrics']['valid_status']}。",
             '按购买时间筛选；商品金额不含运费；订单量去重；数据为所选数据集的固定快照。', '',
             '## 分析说明', *['- ' + note for note in notes], '']
    if style:
        lines += ['说明风格：' + ('简洁；完整计算依据仍保留。' if style == 'concise' else '详细；包含计算方法与依据。'), '']
    if state.get('clarification_answer'):
        lines += ['## 用户补充要求', state['clarification_answer'], '']
    for index, artifact in enumerate(state['artifacts'], 1):
        if artifact['kind'] != 'table':
            continue
        body = read_table(artifact['id'], state['task_id'])
        # 从已保存文件读取全部结果行，不用模型重写表格，也不把有限的 preview 误当完整结果。
        lines += [f"## 结果 {index}：{artifact['label']}", f"来源 ID：{artifact['id']}；总行数：{artifact['row_count']}。", '']
        lines.append('| ' + ' | '.join(map(escape_cell, body['columns'])) + ' |')
        lines.append('| ' + ' | '.join('---' for _ in body['columns']) + ' |')
        lines += ['| ' + ' | '.join(escape_cell(row.get(column)) for column in body['columns']) + ' |' for row in body['rows']]
        if artifact.get('metric_scope', {}).get('enforced'):
            lines += ['', f"执行约束：仅包含状态 {artifact['metric_scope']['valid_status']} 的订单及其商品明细。"]
        lines += ['', '```sql' if artifact.get('sql') else '```python', artifact.get('sql') or artifact.get('python', ''), '```', '']
    lines += ['## 图表来源']
    lines += [f"- {item['label']}：图表 {item['id']}，来源表格 {item['source_id']}；来源 {item['source_row_count']} 行，完整绘制 {item['plotted_row_count']} 行。" for item in state['artifacts'] if item['kind'] == 'chart']
    return write_artifact(state['task_id'], 'report', args['title'], '\n'.join(lines).encode('utf-8'), 'md')
