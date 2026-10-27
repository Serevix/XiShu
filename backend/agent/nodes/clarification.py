"""使用 LangGraph 的 interrupt 暂停分析，等待用户补充必要信息。"""
from langgraph.types import interrupt
from backend.agent.nodes.tools import validate_arguments, feedback_messages


def clarify(state: dict) -> dict:
    """暂停当前流程并询问用户。恢复时该节点会从头执行，因此暂停前不能进行重复写入等操作。"""
    args = state['action']['arguments']
    validate_arguments('ask_clarification', args)
    # 首次执行 interrupt 时，图会保存暂停位置并退出。用户补充答案后恢复节点，interrupt 才返回这份答案。
    # 恢复时会再次执行 interrupt 之前的代码，所以这里只检查参数，不提前写库、发事件或生成文件。
    answer = interrupt({'question': args['question'][:1000]})
    return {'clarification_done': True, 'clarification_answer': str(answer),
            'messages': feedback_messages(state, {'user_answer': str(answer)})}
