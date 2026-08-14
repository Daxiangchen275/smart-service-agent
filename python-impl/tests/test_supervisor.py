# python-impl\tests\test_supervisor.py
"""human_handoff_node 单元测试。"""
import asyncio

from langchain_core.messages import HumanMessage

from agents.supervisor import human_handoff_node, HUMAN_HANDOFF_MESSAGE


def _run(coro):
    """同步运行 async 协程（项目未引入 pytest-asyncio）。"""
    return asyncio.run(coro)


def _state(**overrides):
    """构造最小 AgentState，缺省值按 human_handoff_node 的读取方式对齐。"""
    base = {
        "session_id": "sess-1",
        "messages": [],
        "sub_results": {},
    }
    base.update(overrides)
    return base


def test_handoff_without_messages():
    """无消息时返回固定转人工话术。"""
    result = _run(human_handoff_node(_state()))
    assert result["current_agent"] == "human_handoff"
    assert result["sub_results"]["human_handoff"]["answer"] == HUMAN_HANDOFF_MESSAGE


def test_handoff_with_message():
    """有消息时仍转人工，且带出 agent 标识。"""
    state = _state(messages=[HumanMessage(content="今天天气怎么样？")])
    result = _run(human_handoff_node(state))
    assert result["current_agent"] == "human_handoff"
    assert result["sub_results"]["human_handoff"]["agent"] == "human_handoff"


def test_handoff_preserves_existing_sub_results():
    """不覆盖已有子 Agent 结果，仅追加 human_handoff。

    existing 模拟真实链路中 IntentRouter 已写入的 sub_results 结构，
    与 intent_router.py 里的输出字段（agent/intent/confidence/raw_input/rewritten_input）保持一致。
    """
    existing = {
        "intent_router": {
            "agent": "intent_router",
            "intent": "human_handoff",
            "confidence": 0.42,
            "raw_input": "今天天气怎么样？",
            "rewritten_input": "天气查询",
        }
    }
    result = _run(human_handoff_node(_state(sub_results=existing)))
    # 追加了 human_handoff
    assert "human_handoff" in result["sub_results"]
    # 原有 intent_router 数据完整保留，字段不被覆盖
    assert result["sub_results"]["intent_router"] == existing["intent_router"]
    assert result["sub_results"]["intent_router"]["confidence"] == 0.42
    assert result["sub_results"]["intent_router"]["intent"] == "human_handoff"
