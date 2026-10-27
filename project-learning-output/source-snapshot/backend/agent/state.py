"""定义可以保存到 checkpoint 的分析状态。状态中不保存密钥或数据库连接对象。"""
from typing import TypedDict


class AnalysisState(TypedDict, total=False):
    """描述分析流程使用的消息、工具动作、结果引用、用量、摘要和澄清信息。"""
    # TypedDict 提供类型提示，不负责运行时检查。total=False 允许尚未执行到的步骤暂时没有对应字段。
    # 这里没有配置自动追加列表的规则。同名字段更新时会替换旧值，因此节点追加消息时要保留原列表。
    task_id: str
    dataset_id: str
    question: str
    mode: str
    metrics: dict
    # messages 保存本任务的完整消息，request_messages 保存本次发给模型的精简消息，两者用途不同。
    messages: list[dict]
    # action 保存工具名、参数和调用 ID。artifacts 保存结果文件的信息，完整内容保存在受控文件目录中。
    action: dict
    artifacts: list[dict]
    steps: int
    usage: dict
    schema_loaded: bool
    clarification_done: bool
    clarification_answer: str
    report: dict
    report_artifacts: list[dict]
    report_warning: str
    verification_failures: int
    request_messages: list[dict]
    message_summary: str
    # 这里的 summary_cursor 是消息列表中的切分位置；会话表的摘要进度则按消息序号记录，两者不能混用。
    summary_cursor: int
    summary_error: str
    summary_usage: dict
    personalization: dict
    conversation_id: str
