"""检查报告数字是否有计算依据，以及图表是否包含来源表的全部行。"""
import re
from decimal import Decimal, InvalidOperation
from backend.core.storage import read_table

NUMBER = re.compile(r'(?<![A-Za-z_0-9])[-+]?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][-+]?\d+)?')
DATE = re.compile(r'(\d{4})[-年/](\d{1,2})(?:[-月/](\d{1,2}))?')
MONTH_RANGE = re.compile(r'(\d{1,2})月\s*[-—–~至到]\s*(\d{1,2})月')


def select_report_artifacts(artifacts: list[dict], references: list[str] | None) -> list[dict]:
    """从当前任务产物选取最终结果，未知引用拒绝；图表的真实来源表自动纳入交付校验。"""
    if references is None:
        return artifacts
    if not references or len(references) != len(set(references)):
        raise ValueError('最终结果引用不能为空或重复')
    by_reference = {}
    for item in artifacts:
        by_reference[item['id']] = item
        if item.get('artifact_ref'):
            by_reference[item['artifact_ref']] = item
    selected = []
    for reference in references:
        item = by_reference.get(reference)
        if item is None or item['kind'] not in ('table','chart'):
            raise ValueError('最终结果引用不属于本任务的表格或图表：' + reference)
        if item['id'] not in {row['id'] for row in selected}:
            selected.append(item)
    for chart in list(selected):
        if chart['kind'] == 'chart':
            source = by_reference.get(chart.get('source_id'))
            if source is None or source['kind'] != 'table':
                raise ValueError('图表缺少当前任务中的来源表')
            if source['id'] not in {row['id'] for row in selected}:
                selected.append(source)
    return selected


def mask_month_range(match) -> str:
    """在提取数字前去掉合法月份范围的连接符，避免把月份范围读成负数。非法月份不会被忽略。"""
    return ' ' if all(1 <= int(part) <= 12 for part in match.groups()) else match.group()


def numbers(text: str) -> set[Decimal]:
    """从文字中提取数字并转成 Decimal，保留正负号，并支持千分位格式。"""
    masked = DATE.sub(' ', MONTH_RANGE.sub(mask_month_range, text))
    return known_dates(text) | {Decimal(match.group().replace(',', '')) for match in NUMBER.finditer(masked)}


def known_dates(text: str) -> set[Decimal]:
    """提取文字中明确出现的年月日，作为可写入报告的日期依据，不把其他任意数字当成统计事实。"""
    parts = {Decimal(part) for match in DATE.finditer(text) for part in match.groups() if part}
    parts.update(Decimal(month) for month in re.findall(r'(\d{1,2})月', text) if 1 <= int(month) <= 12)
    parts.update(Decimal(day) for day in re.findall(r'(\d{1,2})日', text) if 1 <= int(day) <= 31)
    return parts


def verify_report_inputs(task_id: str, artifacts: list[dict], notes: list[str], title: str = '',
                         question: str = '', expected_status: str | None = None) -> None:
    """核对结果文件、报告数字、订单状态和图表行数。这些检查不能保证所有业务解释都正确。"""
    tables = [item for item in artifacts if item['kind'] == 'table']
    if not tables or not any(item['row_count'] and any(any(value is not None for value in row.values()) for row in item['preview']) for item in tables):
        raise ValueError('没有有效计算结果，不能生成完成报告')
    if sum(map(len, notes)) > 3000 or len(title) > 120:
        raise ValueError('报告标题限 120 字，说明总长度限 3000 字，请压缩对应字段')
    allowed = known_dates(question)
    # 报告数字应来自实际结果文件或明确日期，用户问题中的其他数字不能自动作为统计依据。
    for item in tables:
        body = read_table(item['id'], task_id)
        if len(body['rows']) != item['row_count'] or body['columns'] != item['columns']:
            raise ValueError('结果文件与产物记录不一致')
        scope = item.get('metric_scope', {})
        if expected_status and item.get('sql') and (not scope.get('enforced') or scope.get('valid_status') != expected_status):
            raise ValueError('SQL 产物缺少当前有效订单状态的执行约束，请重新查询')
        allowed.update(known_dates(item.get('sql', '')))
        allowed.add(Decimal(len(body['rows'])))
        if item.get('metric_id') == 'customer_repeat':
            allowed.add(Decimal(2))
        for row in body['rows']:
            for value in row.values():
                if value is None or isinstance(value, bool):
                    continue
                try:
                    number = Decimal(str(value))
                    if number.is_finite():
                        allowed.update((number, number.quantize(Decimal('.01'))))
                        if number < 0:
                            allowed.update((-number, (-number).quantize(Decimal('.01'))))
                except InvalidOperation:
                    allowed.update(known_dates(str(value)))
    for field, text in [('title', title), *[(f'notes[{index}]', note) for index, note in enumerate(notes)]]:
        if expected_status:
            state_name = re.escape(expected_status)
            excluded = re.search(r'(?:排除|不包含|不含|剔除|未包含|exclude\w*|without)\s*[（(]?\s*' + state_name, text, re.IGNORECASE)
            excluded = excluded or re.search(state_name + r'\s*[）)]?\s*(?:已排除|不计入|未计入)', text, re.IGNORECASE)
            if excluded:
                raise ValueError(f'{field} 的状态说明与实际查询冲突：实际只包含 {expected_status}，不能称排除该状态；请按指标口径重写。')
            if expected_status != 'delivered' and any(word in text for word in ('已交付', '已送达')):
                raise ValueError(f'{field} 错称已交付订单；实际有效状态为 {expected_status}，请使用已确认状态描述。')
        # 数字出现在结果表中，并不代表报告对它的解释一定正确，业务含义仍需独立评测和检查。
        unsupported = numbers(text) - allowed
        if unsupported:
            values = ', '.join(str(value) for value in sorted(unsupported))
            raise ValueError(f'{field} 含无依据数字 {values}；先用 SQL/Python 实际计算并保存这些数字，或删去该说明；不能口算后直接写入。')
    for chart in (item for item in artifacts if item['kind'] == 'chart'):
        # 比较来源表行数与实际绘制行数，避免来源表完整但图表遗漏后面的类别。
        source = read_table(chart['source_id'], task_id)
        count = len(source['rows'])
        if chart.get('source_row_count') != count or chart.get('plotted_row_count') != count or chart.get('truncated') is not False:
            raise ValueError('图表没有完整覆盖来源表格，请重新生成完整图表，不能声称已展示全部结果')
