"""定义 Agent 的执行流程。模型负责选择工具，Worker 负责提供保存和恢复执行进度的组件。"""
from langgraph.graph import StateGraph, START, END
from backend.agent.state import AnalysisState
from backend.agent.nodes.decision import decide
from backend.agent.nodes.tools import execute_tool
from backend.agent.nodes.clarification import clarify
from backend.agent.nodes.verification import verify
from backend.agent.nodes.finalization import finalize
from backend.agent.nodes.context import manage_context


def route_action(state: dict) -> str:
    """读取模型选择的动作，决定接下来执行工具、询问用户，还是检查分析结果。"""
    return {'ask_clarification': 'clarify', 'finish': 'verify'}.get(state['action']['name'], 'tools')


def route_verification(state: dict) -> str:
    """检查通过时生成报告；检查失败时回到模型决策，让模型尝试修正。"""
    return 'finalize' if state['action'].get('verified') else 'context'


def build_graph(checkpointer):
    """创建分析流程，并接入保存执行进度和暂停等待用户的能力。"""
    graph = StateGraph(AnalysisState)
    graph.add_node('context', manage_context)
    graph.add_node('decide', decide)
    graph.add_node('tools', execute_tool)
    graph.add_node('clarify', clarify)
    graph.add_node('verify', verify)
    graph.add_node('finalize', finalize)
    # 每轮先整理符合长度限制的上下文，再让模型选择工具。模型不能自行增加执行节点。
    graph.add_edge(START, 'context')
    graph.add_edge('context', 'decide')
    graph.add_conditional_edges('decide', route_action)
    # 工具执行后把结果交给模型，让模型根据结果决定下一步，从而形成多轮分析循环。
    graph.add_edge('tools', 'context')
    graph.add_edge('clarify', 'context')
    graph.add_conditional_edges('verify', route_verification)
    graph.add_edge('finalize', END)
    # checkpointer 保存图中各节点的状态和暂停位置。用户账号和跨任务聊天历史由其他表保存。
    return graph.compile(checkpointer=checkpointer)
