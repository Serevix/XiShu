"""使用程序预先定义的绘图方式生成 PNG，不执行模型编写的绘图代码。"""
import io
import textwrap
import threading
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from backend.core.storage import read_table, write_artifact


CHART_LOCK = threading.Lock()


def create_chart(task_id: str, artifact_id: str, x: str, y: str, title: str, kind: str = 'bar', currency: str | None = None) -> dict:
    """保护 Matplotlib 共享状态，独立查询仍并发，单进程中的绘图依次完成。"""
    with CHART_LOCK:
        return draw_chart(task_id, artifact_id, x, y, title, kind, currency)


def draw_chart(task_id: str, artifact_id: str, x: str, y: str, title: str, kind: str = 'bar', currency: str | None = None) -> dict:
    """绘制当前任务表格的全部行，并记录来源、行数和方向。数据过多或字段不适合绘图时，明确报错。"""
    if kind not in ('bar', 'line'):
        raise ValueError('仅支持 bar 或 line')
    body = read_table(artifact_id, task_id)
    if x not in body['columns'] or y not in body['columns'] or not body['rows']:
        raise ValueError('图表列不存在或结果为空')
    rows = body['rows']
    # 单图数据超过上限时要求用户聚合或分组，不偷偷截取部分行。需要 Top N 时应先查询对应范围。
    if len(rows) > 200:
        raise ValueError(f'完整图表包含 {len(rows)} 行，超出单图可读上限 200；请明确聚合或分组后分别绘图，不会自动截取。')
    labels = [str(row[x]) for row in rows]
    if len(set(labels)) != len(labels):
        raise ValueError('图表横轴维度存在重复值，请先按该维度聚合或分别查询各系列')
    numbers = [float(row[y]) for row in rows]
    if not all(__import__('math').isfinite(value) for value in numbers):
        raise ValueError('图表存在非有限数值')
    chinese_font = Path('C:/Windows/Fonts/msyh.ttc')
    font = font_manager.FontProperties(fname=str(chinese_font)) if chinese_font.exists() else None
    horizontal = kind == 'bar' and len(rows) > 16
    # 类别多时使用横向条形图，并按类别数增加图的高度，让后面的类别和标签也能显示。
    size = (12, max(5, len(rows) * .25 + 1)) if horizontal else (max(9, min(20, len(rows) * .3)), 4.5)
    fig, axis = plt.subplots(figsize=size, layout='constrained')
    try:
        if horizontal:
            axis.barh(range(len(rows)), numbers, color='#176f64')
            axis.set_yticks(range(len(rows)), labels=[textwrap.fill(label, 35) for label in labels], fontproperties=font, fontsize=8)
            axis.invert_yaxis()
        elif kind == 'bar':
            axis.bar(labels, numbers, color='#176f64')
        else:
            axis.plot(labels, numbers, color='#176f64', marker='o')
        axis.set_title(title[:120], fontproperties=font)
        units = f'{y} ({currency})' if currency and any(word in y.lower() for word in ('amount', 'sales', 'price', 'aov')) else y
        if horizontal:
            axis.set_xlabel(units, fontproperties=font)
        else:
            axis.set_ylabel(units, fontproperties=font)
            axis.tick_params(axis='x', rotation=25)
            if font:
                for label in axis.get_xticklabels():
                    label.set_fontproperties(font)
        axis.spines[['top', 'right']].set_visible(False)
        stream = io.BytesIO()
        fig.savefig(stream, format='png', dpi=140)
        return write_artifact(task_id, 'chart', title, stream.getvalue(), 'png', source_id=artifact_id, x=x, y=y,
                              source_row_count=len(rows), plotted_row_count=len(rows), truncated=False,
                              chart_kind=kind, orientation='horizontal' if horizontal else 'vertical')
    finally:
        # 每次绘图后都关闭 figure，避免长期运行的 Worker 不断累积图对象、占用内存。
        plt.close(fig)
