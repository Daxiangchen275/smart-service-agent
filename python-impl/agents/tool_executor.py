# ============================================================
# ToolExecutor 工具执行 Agent
# ============================================================

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

import time as _time

from tracing.otel_config import trace_agent_call
from scripts.skill_runtime import SkillToolSpec, find_tool_by_name, invoke_skill_tool
from tracing.collector import collector

logger = logging.getLogger(__name__)

# ── Prompt ──

TOOL_ANSWER_PROMPT = """你是智能客服工具执行助手。将以下工具执行结果转换为用户友好的自然语言回复。

要求：
- 用礼貌、清晰的中文表达
- 突出关键信息（订单状态、物流编号、用户等级、风控结果等）
- 如果结果为空或失败，礼貌说明并建议下一步操作
- 不要编造结果中没有的信息

用户问题：{question}

工具执行结果：
{tool_results}

请生成回复："""


# ── 工具名 → 降级格式化函数 ──

def _fmt_order(data: dict) -> str:
    """格式化 order_query 结果（兼容单订单 + 用户订单列表）。"""
    # 用户订单列表格式: {"orders": [...], "count": N}
    if "orders" in data:
        orders = data.get("orders", [])
        lines = [f"共 {data.get('count', len(orders))} 笔进行中订单："]
        for o in orders:
            line = f"  {o.get('order_id','?')} [{o.get('status','?')}] ¥{o.get('amount',0)}"
            loc = o.get('current_location', '')
            if loc:
                line += f" — {loc}"
            ed = o.get('estimated_delivery', '')
            if ed:
                line += f"（预计 {ed}）"
            lines.append(line)
        return "\n".join(lines)

    # 单订单格式: {"order": {...}}
    order = data.get("order", {})
    parts = [
        f"订单号: {order.get('order_id', 'N/A')}",
        f"商品: {order.get('product', '未知')}",
        f"金额: {order.get('amount', 0)} 元",
        f"订单状态: {order.get('status', '未知')}",
    ]
    logistics = order.get('logistics', '')
    if logistics and logistics != '暂无':
        parts.append(f"物流: {logistics}")
    ls = order.get('logistics_status', '')
    if ls:
        parts.append(f"物流状态: {ls}")
    loc = order.get('current_location', '')
    if loc:
        parts.append(f"当前位置: {loc}")
    ed = order.get('estimated_delivery', '')
    if ed:
        parts.append(f"预计送达: {ed}")
    payment = order.get('payment', '')
    if payment:
        parts.append(f"支付: {payment}")
    return "；".join(parts)


def _fmt_user(data: dict) -> str:
    """格式化 user_profile 结果。"""
    profile = data.get("profile", {})
    return (
        f"用户 {profile.get('user_id', 'N/A')}：{profile.get('level', '未知')} 会员，"
        f"余额 {profile.get('balance', 0)} 元，"
        f"积分 {profile.get('points', 0)}"
    )


def _fmt_risk(data: dict) -> str:
    """格式化 risk_check 结果。"""
    level = data.get("risk_level", "unknown")
    allow = "通过" if data.get("allow") else "不通过"
    reasons = "；".join(data.get("reasons", []))
    tail = f"（{reasons}）" if reasons else ""
    return f"风控评估：{level} 级，{allow}{tail}"


def _fmt_ticket_update(data: dict) -> str:
    """格式化 ticket_update 结果。"""
    ticket = data.get("ticket", {})
    return f"工单 {ticket.get('ticket_id', 'N/A')} 已更新，当前状态：{ticket.get('status', '未知')}"


# 工具名 → 降级格式化函数映射
_FALLBACK_FORMATTERS: dict[str, Callable[[dict], str]] = {
    "order_query":   _fmt_order,
    "user_profile":  _fmt_user,
    "risk_check":    _fmt_risk,
    "ticket_update": _fmt_ticket_update,
}


# ── Agent ──

class ToolExecutorAgent:
    """工具执行 Agent：通过 SKILL.md 自主规划工具调用，批量执行后将结果转为自然语言回复。"""

    # 支持的工具名集合
    SUPPORTED_TOOLS = {"order_query", "user_profile", "risk_check", "ticket_update"}

    def __init__(self, llm: BaseChatModel, skill_tools: list[SkillToolSpec]) -> None:
        self._llm = llm
        self._skill_tools = skill_tools          # skill_runtime 解析的工具列表

    # ── LangGraph 节点入口 ──

    @trace_agent_call("ToolExecutor")
    async def process(self, state: dict[str, Any]) -> dict[str, Any]:
        """LangGraph 节点入口：自主规划工具调用并生成回复。"""
        messages = state.get("messages", [])
        user_id = state.get("user_id", "user-1001")

        if not messages:
            return self._empty_result()

        user_text = self._extract_text(messages[-1])

        # 1. 自主分析用户消息，匹配工具
        intent_result = state.get("intent_result", {})
        entities = intent_result.get("entities", {})
        tool_calls = self._plan_tools(user_text, user_id, entities)

        # 2. 批量调用 Skill 工具
        tool_results = await self.run_tools(tool_calls)

        # 3. LLM 将 JSON 结果转为自然语言
        answer = await self.generate_answer(user_text, tool_results)

        existing_sub = state.get("sub_results", {})
        return {
            "sub_results": {
                **existing_sub,
                "tool_executor": {
                    "agent": "tool_executor",
                    "results": tool_results,
                    "answer": answer,
                }
            },
            "current_agent": "tool_executor",
        }

    # ── 工具调用 ──

    async def run_tools(self, tool_calls: list[dict]) -> list[dict[str, Any]]:
        """批量串行执行工具（skill_runtime 子进程调用）。

        Returns:
            [{"tool": str, "success": bool, "result": Any, "error": str|None, "duration_ms": float}, ...]
        """
        results: list[dict[str, Any]] = []

        for tc in tool_calls:
            name = tc.get("name", "")
            args = tc.get("arguments", {})

            # 跳过未注册或不受支持的工具
            if name not in self.SUPPORTED_TOOLS:
                logger.debug("Tool '%s' not in SUPPORTED_TOOLS, skipped", name)
                continue

            results.append(await self._call_tool(name, args))

        return results

    async def _call_tool(self, name: str, args: dict) -> dict[str, Any]:
        """通过 skill_runtime 查找并执行 SKILL.MD 中定义的脚本工具。"""
        tool_spec = find_tool_by_name(name, self._skill_tools)
        if tool_spec is None:
            collector.record_tool_call(name, 0, False)
            return {"tool": name, "success": False,
                    "error": f"工具 '{name}' 未在 SKILL.MD 中注册"}

        t0 = _time.perf_counter()
        try:
            raw = invoke_skill_tool(tool_spec, payload=args)
            duration_ms = (_time.perf_counter() - t0) * 1000
            ok = raw.get("ok", False)
            collector.record_tool_call(name, duration_ms, ok)
            if ok:
                return {"tool": name, "success": True,
                        "result": raw.get("result", {}), "error": None}
            else:
                logger.warning("skill_runtime '%s' FAIL: stderr=%s",
                              name, raw.get("stderr", "")[:300])
                return {"tool": name, "success": False,
                        "result": None, "error": raw.get("error", "skill_runtime 执行失败")}
        except Exception as exc:
            duration_ms = (_time.perf_counter() - t0) * 1000
            logger.warning("skill_runtime '%s' crashed: %s", name, exc)
            collector.record_tool_call(name, duration_ms, False)
            return {"tool": name, "success": False, "error": str(exc)}

    # ── 自主规划 ──

    def _plan_tools(self, user_text: str, user_id: str,
                     entities: dict[str, str] | None = None) -> list[dict[str, Any]]:
        """当 IntentRouter 未预规划工具时，根据用户消息关键词 + IntentRouter 实体自主匹配工具。

        优先级: IntentRouter 提取的 entities > 正则提取 > 空字符串
        正则兼容两种订单号格式: ORD-20260701-001 和 ORD-20260701-A0001

        Returns:
            [{"name": str, "arguments": {...}}, ...]
        """
        import re
        entities = entities or {}
        tools: list[dict[str, Any]] = []

        # 订单号来源: IntentRouter 实体优先, 正则兜底
        order_id = entities.get("order_id", "")
        if not order_id:
            # 兼容两种格式: ORD-8位数字-3位数字 或 ORD-8位数字-大写字母+数字
            order_match = re.search(r'ORD-\d{8}-[A-Z0-9]{3,6}', user_text)
            if order_match:
                order_id = order_match.group(0)

        # 物流单号来源: IntentRouter 实体优先（tracking_number）, 正则兜底
        tracking_no = entities.get("tracking_number", "")
        if not tracking_no:
            tn_match = re.search(r'(?:SF|YT|ZTO|JD|YTO|HTKY|STO)\d{6,20}', user_text)
            if tn_match:
                tracking_no = tn_match.group(0)

        # 订单/物流相关关键词 → 调用 order_query
        if order_id or tracking_no or any(kw in user_text for kw in
                           ["订单", "物流", "快递", "发货", "收货", "到哪", "单号"]):
            tools.append({
                "name": "order_query",
                "arguments": {
                    "order_id": order_id,
                    "user_id": user_id,
                    "tracking_no": tracking_no,
                },
            })

        # 用户画像
        if any(kw in user_text for kw in ["账户", "会员", "余额", "积分", "个人信息", "用户信息", "等级", "资料"]):
            tools.append({
                "name": "user_profile",
                "arguments": {"user_id": user_id},
            })

        # 风控检查：包含金额数字
        amount_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:元|块|万)', user_text)
        if any(kw in user_text for kw in ["风控", "风险", "大额", "转账"]) or (
           amount_match and float(amount_match.group(1)) > 5000):
            amount = float(amount_match.group(1)) if amount_match else 0
            tools.append({
                "name": "risk_check",
                "arguments": {"user_id": user_id, "amount": amount},
            })

        # 工单更新: IntentRouter 实体优先, 正则兜底
        ticket_id = entities.get("ticket_id", "")
        if not ticket_id:
            ticket_match = re.search(r'TKT?-\d{8}-[A-Z0-9]{4,6}', user_text)
            if ticket_match:
                ticket_id = ticket_match.group(0)

        if ticket_id and any(kw in user_text for kw in
                             ["修改", "更新", "加急", "催单", "优先级"]):
            priority = "urgent" if any(kw in user_text for kw in ["加急", "紧急"]) else "high"
            tools.append({
                "name": "ticket_update",
                "arguments": {
                    "ticket_id": ticket_id,
                    "priority": priority,
                },
            })

        return tools

    # ── 回复生成 ──

    async def generate_answer(self, question: str, tool_results: list[dict]) -> str:
        """将工具返回的数据转为自然语言回复。优先使用 LLM 生成（支持流式输出），
        LLM 失败时降级为规则格式化。"""
        if not tool_results:
            fallback = "没有需要执行的工具操作，请提供更多信息以便我为您服务。"
            # 仍然尝试 LLM 生成更友好的回复（服务于 SSE 流式输出）
            try:
                prompt = (
                    f"用户说：「{question}」\n\n"
                    f"当前没有可用的工具执行结果。请用客服口吻，礼貌告知用户当前无法查询到相关信息，"
                    f"并建议用户提供更多细节（如订单号、工单号等）以便进一步协助。"
                )
                response = await self._llm.ainvoke([HumanMessage(content=prompt)])
                from tracing.otel_config import capture_llm_tokens
                capture_llm_tokens(response)
                answer = self._extract_text(response)
                if answer and answer.strip():
                    return answer.strip()
            except Exception as exc:
                logger.warning("LLM answer generation failed: %s, falling back to rule-based", exc)
            return fallback

        # 优先 LLM 生成自然语言回复（服务于 SSE 流式输出）
        try:
            result_text = json.dumps(
                [{"tool": r.get("tool", ""), "result": r.get("result", {})}
                 for r in tool_results if r.get("success")],
                ensure_ascii=False, indent=2,
            )
            prompt = TOOL_ANSWER_PROMPT.format(
                question=question, tool_results=result_text,
            )
            response = await self._llm.ainvoke([HumanMessage(content=prompt)])
            from tracing.otel_config import capture_llm_tokens
            capture_llm_tokens(response)
            answer = self._extract_text(response)
            if answer and answer.strip():
                return answer.strip()
        except Exception as exc:
            logger.warning("LLM answer generation failed: %s, falling back to rule-based", exc)

        # 降级：规则格式化
        return self._fallback_format(tool_results)

    def _fallback_format(self, results: list[dict]) -> str:
        """纯规则格式化，每种工具对应一个格式化函数。"""
        parts: list[str] = []
        for r in results:
            tool = r.get("tool", "")
            if r.get("success"):
                data = r.get("result", {})
                fmt = _FALLBACK_FORMATTERS.get(tool)
                parts.append(fmt(data) if fmt else str(data))
            else:
                parts.append(f"{tool} 查询失败：{r.get('error', '未知错误')}")

        return "\n".join(parts) if parts else "查询完成，但未获取到有效数据。"

    # ── 工具方法 ──

    @staticmethod
    def _extract_text(msg: Any) -> str:
        """安全提取消息文本。"""
        return msg.content if hasattr(msg, "content") else str(msg)

    def _empty_result(self) -> dict[str, Any]:
        """返回空结果占位。"""
        return {
            "sub_results": {"tool_executor": {"agent": "tool_executor", "answer": ""}},
            "current_agent": "tool_executor",
        }
