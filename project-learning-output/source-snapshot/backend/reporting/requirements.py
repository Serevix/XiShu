"""提取用户明确要求的结果列和计算工具，在报告生成前检查这些要求是否完成。"""
import re


def requested_columns(question: str) -> set[str]:
    """提取用户明确列出的结果列名，排除日期格式说明，不猜测用户没有指定的列名。"""
    match = re.search(r'(?:最终结果表列名|最终列名|结果表列名|结果列名|列名)\s*(?:使用|为|是|：|:)?\s*([^。；;\n]+)', question)
    if not match:
        return set()
    remaining = match.group(1).strip()
    columns = set()
    while remaining:
        field = re.match(r'([A-Za-z_][A-Za-z_0-9]*)(?:\s*[（(][^）)]*[）)])?', remaining)
        if not field:
            break
        columns.add(field.group(1))
        remaining = remaining[field.end():].lstrip()
        if not remaining or remaining[0] not in ',，、':
            break
        remaining = remaining[1:].lstrip()
    return columns


def python_requested(question: str) -> bool:
    """判断用户是否明确要求使用 Python。否定使用 Python 的表达不算要求。"""
    normalized = question.lower()
    return 'python' in normalized and not re.search(r'(?:不用|不要|无需|不使用|without|do not use|don.t use)\s*(?:使用\s*)?python', normalized)


def chart_requested(question: str) -> bool:
    """识别明确绘图要求，避免用户要求图表而模型仅交付表格；否定绘图的表达不计入。"""
    text = re.sub(r'(?:不要|不用|无需|不需要|不绘制|不画)\s*(?:生成|绘制|画)?\s*(?:折线图|柱状图|条形图|图表|绘图|画图|图)', '', question.lower())
    return bool(re.search(r'折线图|柱状图|条形图|图表|绘图|画图|绘制.*图', text))


def chart_forbidden(question: str) -> bool:
    """识别明确禁止所有绘图的要求；只否定某种图而要求另一种图，不等于全部禁图。"""
    return bool(re.search(r'(?:不要|不用|无需|不需要|不绘制|不画)\s*(?:生成|绘制|画)?\s*(?:图表|绘图|画图|图)', question)) and not chart_requested(question)
