# python-impl\agents\supervisor.py
# ============================================================
# Supervisor 编排 Agent — LangGraph StateGraph 构建
# ============================================================

from __future__ import annotations

import logging
from typing import Any, Literal, Annotated

from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.message import add_messages

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage
from typing_extensions import TypedDict

from agents.intent_router import IntentRouterAgent
from agents.knowledge_rag import KnowledgeRAGAgent
from agents.ticket_handler import TicketHandlerAgent
from agents.tool_executor import ToolExecutorAgent
from agents.compliance_checker import ComplianceCheckerAgent
from tracing.otel_config import trace_agent_call
from scripts.skill_runtime import SkillToolSpec

logger = logging.getLogger(__name__)


# ── Reducer：深度合并 sub_results，避免后节点覆盖前节点数据 ──

def _merge_sub_results(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """将 right 深度合并到 left 中，返回新 dict。"""
    merged = dict(left)
    for key, value in right.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


# ── AgentState 共享状态 ──

class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]  # 对话消息（自动追加）
    user_id: str
    session_id: str
    intent: str                           # 路由目标 Agent
    intent_result: dict[str, Any]         # 意图识别详情
    tool_calls: list[dict[str, Any]]      # 待执行的 mcp 工具列表
    tool_results: dict[str, Any]          # 工具执行结果
    sub_results: dict[str, Any]           # 各子 Agent 输出（Agent 内部手动合并，不使用 reducer 避免跨轮累积）
    compliance_passed: bool
    final_response: str
    current_agent: str
    retry_count: int


# ── 路由函数 ──

def route_from_intent(state: AgentState) -> Literal["knowledge_rag", "ticket_handler", "tool_executor", "human_handoff"]:
    """根据意图路由到对应子 Agent。"""
    intent = state.get("intent", "human_handoff")
    if intent in ("knowledge_rag", "ticket_handler", "tool_executor", "human_handoff"):
        return intent
    # 默认走人工客服
    logger.warning("Unknown intent '%s', defaulting to human_handoff", intent)
    return "human_handoff"


# ── 人工转接节点 ──

HUMAN_HANDOFF_MESSAGE = (
    "非常抱歉，我暂时无法准确理解您的问题。"
    "已为您记录需求并转接人工客服，请稍候，我们的客服专员将尽快与您联系。"
    "如有紧急问题，您也可拨打客服热线 400-xxx-xxxx。"
)


@trace_agent_call("HumanHandoff")
async def human_handoff_node(state: AgentState) -> dict[str, Any]:
    """人工转接节点：当 IntentRouter 无法识别用户意图时，返回转人工话术。"""
    session_id = state.get("session_id", "")
    user_text = ""
    messages = state.get("messages", [])
    if messages:
        last_msg = messages[-1]
        user_text = last_msg.content if hasattr(last_msg, "content") else str(last_msg)

    logger.warning("Human handoff triggered for session=%s, user_text[:200]=%s",
                   session_id, user_text[:200])

    existing_sub = state.get("sub_results", {})
    return {
        "sub_results": {
            **existing_sub,
            "human_handoff": {
                "agent": "human_handoff",
                "answer": HUMAN_HANDOFF_MESSAGE,
            }
        },
        "current_agent": "human_handoff",
    }


# ── Supervisor 节点 ──

class SupervisorNode:
    """Supervisor 编排节点：汇总子 Agent 结果，更新工作记忆。"""

    def __init__(self, llm: BaseChatModel, working_memory) -> None:
        self._llm = llm
        self._working_memory = working_memory

    @trace_agent_call("Supervisor")
    async def synthesize_response(self, state: AgentState) -> dict[str, Any]:
        """汇总子 Agent 结果，生成最终回复。"""
        sub_results = state.get("sub_results", {})
        compliance_passed = state.get("compliance_passed", True)
        session_id = state.get("session_id", "")
        intent = state.get("intent", "")

        # 1. 合规失败 → 固定转人工话术
        if not compliance_passed:
            final = "您的问题需要人工客服进一步处理，正在为您转接，请稍候..."
            self._update_working_memory(session_id, state, final)
            return {"final_response": final, "current_agent": "supervisor"}

        # 2. 提取各 Agent 的回答
        answers = {}
        for key, value in sub_results.items():
            if isinstance(value, dict) and "answer" in value and value["answer"]:
                answers[key] = value["answer"]

        # 3. 只有一个子 Agent 有结果 → 直接返回
        if len(answers) == 1:
            final = list(answers.values())[0]
            self._update_working_memory(session_id, state, final)
            return {"final_response": final, "current_agent": "supervisor"}

        # 4. 多个子 Agent 有结果 → LLM 整合
        if len(answers) > 1:
            try:
                final = await self._synthesize_with_llm(answers)
                self._update_working_memory(session_id, state, final)
                return {"final_response": final, "current_agent": "supervisor"}
            except Exception as exc:
                logger.warning("LLM synthesis failed: %s, using first answer", exc)
                final = list(answers.values())[0]
                self._update_working_memory(session_id, state, final)
                return {"final_response": final, "current_agent": "supervisor"}

        # 5. 无结果
        final = "抱歉，我暂时无法处理您的问题，请尝试其他方式或转接人工客服。"
        self._update_working_memory(session_id, state, final)
        return {"final_response": final, "current_agent": "supervisor"}

    async def _synthesize_with_llm(self, answers: dict[str, str]) -> str:
        """调用 LLM 将多个子 Agent 的回答整合为一段连贯回复。"""
        parts = []
        for agent_name, answer in answers.items():
            label = {
                "knowledge_rag": "知识库检索结果",
                "ticket_handler": "工单处理结果",
                "tool_executor": "工具查询结果",
                "human_handoff": "人工转接结果",
            }.get(agent_name, agent_name)
            parts.append(f"## {label}\n{answer}")

        combined = "\n\n".join(parts)
        prompt = f"""以下是多个客服助手对用户问题的回复，请将它们整合为一段连贯、礼貌的自然语言回复。

要求：
- 按照逻辑顺序组织信息
- 去除重复内容
- 保持专业、友好的语气
- 不要添加未在以下内容中出现过的信息

{combined}

整合后的回复："""

        response = await self._llm.ainvoke([HumanMessage(content=prompt)])
        from tracing.otel_config import capture_llm_tokens
        capture_llm_tokens(response)
        return self._extract_text(response)

    @staticmethod
    def _extract_text(msg: Any) -> str:
        """安全提取 LangChain 消息文本（content 可能是 str | list）。"""
        content = msg.content if hasattr(msg, "content") else str(msg)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                item.get("text", "") if isinstance(item, dict) else str(item)
                for item in content
            )
        return str(content)

    def _update_working_memory(self, session_id: str, state: AgentState,
                                final_response: str) -> None:
        """更新工作记忆。"""
        try:
            self._working_memory.update(session_id, {
                "last_intent": state.get("intent", ""),
                "intent_result": state.get("intent_result", {}),
                "final_response": final_response[:500],
            })
        except Exception as exc:
            logger.warning("Failed to update working memory: %s", exc)


# ── 图构建工厂 ──

def create_supervisor_graph(
    streaming_llm: BaseChatModel,
    internal_llm: BaseChatModel,
    working_memory,
    short_term_memory,
    knowledge_store,
    skill_tools: list[SkillToolSpec],
    compliance_enabled: bool = True,
    sensitive_words: list[str] | None = None,
) -> CompiledStateGraph:
    """构建 LangGraph 编排图。

    双模型架构：
    - streaming_llm: 用于用户可见的回复生成（SSE token-by-token 流式输出）
    - internal_llm:  用于内部 LLM 调用（意图分类/合规审查等），非流式确保 token 统计准确

    使用 MemorySaver 作为 checkpointer（进程内内存）。

    Args:
        streaming_llm: 流式 ChatModel（给用户可见的 LLM 调用）
        internal_llm:  非流式 ChatModel（给内部调用，token 可追踪）
        working_memory: WorkingMemory 实例
        short_term_memory: ShortTermMemory 实例
        knowledge_store: ChromaKnowledgeStore 实例
        skill_tools: skill_runtime.load_skill_tools() 解析的工具列表
        compliance_enabled: 是否启用合规审查
        sensitive_words: 合规敏感词列表

    Returns:
        编译后的 StateGraph
    """
    # ── 创建 Agent 实例 ──
    # IntentRouter: 内部调用（意图分类+上下文改写），用 internal_llm 确保 token 统计
    intent_router = IntentRouterAgent(internal_llm)
    # KnowledgeRAG: 内部调用(internal_llm) + 用户可见回复(streaming_llm)
    knowledge_rag = KnowledgeRAGAgent(streaming_llm, knowledge_store, internal_llm=internal_llm)
    # TicketHandler: 内部分析(internal_llm) + 用户可见回复(streaming_llm)
    ticket_handler = TicketHandlerAgent(streaming_llm, skill_tools, internal_llm=internal_llm)
    # ToolExecutor: 用户可见回复生成，用 streaming_llm（SSE 流式输出）
    tool_executor = ToolExecutorAgent(streaming_llm, skill_tools)
    # ComplianceChecker: 纯内部调用，用 internal_llm 确保 token 统计
    compliance_checker = ComplianceCheckerAgent(
        llm=internal_llm,
        sensitive_words=sensitive_words,
        enabled=compliance_enabled,
    )
    supervisor_node = SupervisorNode(streaming_llm, working_memory)

    # ── 构建图 ──
    workflow = StateGraph(AgentState)

    # 添加节点
    workflow.add_node("intent_router", intent_router.process)
    workflow.add_node("knowledge_rag", knowledge_rag.process)
    workflow.add_node("ticket_handler", ticket_handler.process)
    workflow.add_node("tool_executor", tool_executor.process)
    workflow.add_node("human_handoff", human_handoff_node)
    workflow.add_node("compliance_check", compliance_checker.process)
    workflow.add_node("synthesize", supervisor_node.synthesize_response)

    # 设置入口
    workflow.set_entry_point("intent_router")

    # 条件路由：intent_router → 子 Agent
    workflow.add_conditional_edges(
        "intent_router",
        route_from_intent,
        {
            "knowledge_rag": "knowledge_rag",
            "ticket_handler": "ticket_handler",
            "tool_executor": "tool_executor",
            "human_handoff": "human_handoff",
        },
    )

    # 子 Agent → compliance_check
    workflow.add_edge("knowledge_rag", "compliance_check")
    workflow.add_edge("ticket_handler", "compliance_check")
    workflow.add_edge("tool_executor", "compliance_check")
    workflow.add_edge("human_handoff", "compliance_check")

    # compliance_check → synthesize → END
    workflow.add_edge("compliance_check", "synthesize")
    workflow.add_edge("synthesize", END)

    # 编译（MemorySaver: 进程内内存 checkpointer，服务重启后会话状态丢失）
    checkpointer = MemorySaver()
    graph = workflow.compile(checkpointer=checkpointer)

    logger.info("Supervisor graph compiled with %d nodes",
                len(workflow.nodes) if hasattr(workflow, 'nodes') else 8)
    return graph
