# ============================================================
# ComplianceChecker 合规审查 Agent
# ============================================================

from __future__ import annotations

import re
import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from tracing.otel_config import trace_agent_call
from tracing.collector import collector

logger = logging.getLogger(__name__)

COMPLIANCE_LLM_PROMPT = """你是金融/电商合规审查专家。审查以下客服回复内容是否存在合规风险：

审查维度：
1. 是否有虚假承诺（如"保证收益""稳赚不赔"）
2. 是否泄露用户敏感个人信息（手机号、身份证号、银行卡号、邮箱）
3. 是否有误导性陈述
4. 是否缺少必要的风险提示（金融产品）

返回 JSON：
{
  "compliant": true/false,
  "risk_level": "low"/"medium"/"high"/"critical",
  "issues": ["问题1", "问题2"],
  "suggestions": ["建议1"]
}

待审查内容：
{content}
"""


class ComplianceCheckerAgent:
    """合规审查 Agent：规则引擎 + LLM 深度审查两阶段机制。"""

    def __init__(self, llm: BaseChatModel | None = None,
                 sensitive_words: list[str] | None = None,
                 enabled: bool = True) -> None:
        self._llm = llm
        self._sensitive_words = sensitive_words or [
            "保证收益", "稳赚不赔", "零风险", "保本保息",
            "绝对安全", "百分百", "100%", "必涨", "内幕",
        ]
        self._enabled = enabled

        # PII 正则
        self._pii_patterns = {
            "手机号": re.compile(r'1[3-9]\d{9}'),
            "身份证号": re.compile(r'\d{17}[\dXx]'),
            "银行卡号": re.compile(r'\d{16,19}'),
            "邮箱": re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'),
        }

    @trace_agent_call("ComplianceChecker")
    async def process(self, state: dict[str, Any]) -> dict[str, Any]:
        """LangGraph 节点入口。

        汇总所有 sub_results 文本内容进行审查，包括数据归属权校验。
        """
        if not self._enabled:
            return {"compliance_passed": True}

        session_user_id = state.get("user_id", "")
        sub_results = state.get("sub_results", {})

        # 0. 数据归属权校验：确保返回的订单/工单属于当前用户
        ownership_issues = self._check_ownership(sub_results, session_user_id)
        if ownership_issues:
            logger.warning("Compliance: ownership violation — %s", ownership_issues)
            collector.record_compliance(False)
            # 脱敏处理：清除越权数据
            masked_sub = self._mask_unauthorized_data(sub_results, session_user_id)
            return {
                "compliance_passed": False,
                "sub_results": masked_sub,
            }

        # 汇总所有子 Agent 的输出文本
        all_text = self._collect_text(sub_results)
        if not all_text:
            return {"compliance_passed": True}

        # 1. 规则引擎（毫秒级）
        rule_result = self.rule_check(all_text)

        # 2. LLM 深度审查（语义违规）
        llm_issues: list[str] = []
        if self._llm and rule_result["risk_level"] not in ("critical",):
            llm_result = await self.llm_check(all_text)
            llm_issues = llm_result.get("issues", [])

        # 汇总
        all_issues = rule_result["issues"] + llm_issues
        risk_level = rule_result["risk_level"]
        if llm_issues:
            risk_level = self._max_risk(risk_level, "medium")

        compliance_passed = risk_level not in ("high", "critical")
        collector.record_compliance(compliance_passed)

        # 若未通过，对 sub_results 做脱敏处理
        if not compliance_passed:
            masked_text = self._mask_pii(all_text)
            # 将脱敏内容写回第一个 sub_result 的 answer
            for key in sub_results:
                if isinstance(sub_results[key], dict) and "answer" in sub_results[key]:
                    sub_results[key]["answer"] = masked_text
                    break

        return {
            "compliance_passed": compliance_passed,
            "sub_results": sub_results,
        }

    def rule_check(self, text: str) -> dict[str, Any]:
        """规则引擎检查：敏感词 + PII 检测。"""
        issues: list[str] = []
        risk_level = "low"

        # 敏感词检测
        for word in self._sensitive_words:
            if word in text:
                issues.append(f"包含敏感词: {word}")
                risk_level = self._max_risk(risk_level, "high")

        # PII 检测
        for pii_type, pattern in self._pii_patterns.items():
            matches = pattern.findall(text)
            if matches:
                # 排除脱敏后的值（已含 *** 的）
                real_matches = [m for m in matches if "***" not in m]
                if real_matches:
                    issues.append(f"可能泄露{ pii_type}: {real_matches[:3]}")
                    risk_level = self._max_risk(risk_level, "critical")

        return {"issues": issues, "risk_level": risk_level}

    async def llm_check(self, text: str) -> dict[str, Any]:
        """LLM 深度审查：处理规则无法覆盖的语义违规。"""
        if not self._llm:
            return {"compliant": True, "risk_level": "low", "issues": [], "suggestions": []}

        try:
            prompt = COMPLIANCE_LLM_PROMPT.replace("{content}", text[:4000])
            response = await self._llm.ainvoke([
                SystemMessage(content="你是合规审查专家。只输出 JSON。"),
                HumanMessage(content=prompt),
            ])
            from tracing.otel_config import capture_llm_tokens
            capture_llm_tokens(response)
            resp_text = self._extract_text(response)
            return self._parse_json(resp_text)
        except Exception as exc:
            logger.warning("LLM compliance check failed: %s", exc)
            return {"compliant": True, "risk_level": "low", "issues": [], "suggestions": []}

    def _mask_pii(self, text: str) -> str:
        """对检测到的敏感信息做掩码处理。"""
        # 手机号：138****8001
        text = re.sub(r'(1[3-9]\d)\d{4}(\d{4})', r'\1****\2', text)
        # 身份证号
        text = re.sub(r'(\d{3})\d{11}(\d{3}[\dXx])', r'\1***********\2', text)
        # 银行卡号
        text = re.sub(r'(\d{4})\d{8,15}(\d{4})', r'\1****\2', text)
        # 邮箱
        text = re.sub(r'([a-zA-Z0-9._%+-]{2})[a-zA-Z0-9._%+-]*(@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})',
                      r'\1***\2', text)
        return text

    def _check_ownership(self, sub_results: dict[str, Any], session_user_id: str) -> list[str]:
        """检查 sub_results 中所有返回数据的 user_id 是否与当前会话一致。

        遍历 ticket_handler / tool_executor 的 results 列表，
        提取 order/ticket 中的 user_id 与 session_user_id 比对。
        """
        if not session_user_id:
            return []

        issues: list[str] = []
        for agent_key, agent_value in sub_results.items():
            if not isinstance(agent_value, dict):
                continue
            results = agent_value.get("results", [])
            if not isinstance(results, list):
                continue
            for r in results:
                if not isinstance(r, dict) or not r.get("success"):
                    continue
                data = r.get("result", {})
                if not isinstance(data, dict):
                    continue
                # 检查单条订单
                order = data.get("order", {})
                if isinstance(order, dict) and order.get("user_id"):
                    if order["user_id"] != session_user_id:
                        issues.append(
                            f"订单 {order.get('order_id','?')} 属于 {order['user_id']}，"
                            f"当前用户 {session_user_id} 无权访问"
                        )
                # 检查单条工单
                ticket = data.get("ticket", {})
                if isinstance(ticket, dict) and ticket.get("user_id"):
                    if ticket["user_id"] != session_user_id:
                        issues.append(
                            f"工单 {ticket.get('ticket_id','?')} 属于 {ticket['user_id']}，"
                            f"当前用户 {session_user_id} 无权访问"
                        )
                # 检查用户订单列表
                orders = data.get("orders", [])
                if isinstance(orders, list):
                    for o in orders:
                        if isinstance(o, dict) and o.get("user_id"):
                            if o["user_id"] != session_user_id:
                                issues.append(
                                    f"订单列表含 {o.get('order_id','?')} 属于 {o['user_id']}，"
                                    f"当前用户 {session_user_id} 无权访问"
                                )
        return issues

    def _mask_unauthorized_data(self, sub_results: dict[str, Any],
                                 session_user_id: str) -> dict[str, Any]:
        """将越权数据替换为无权限提示，不清空其他合规数据。"""
        import copy
        masked = copy.deepcopy(sub_results)
        for agent_key, agent_value in masked.items():
            if not isinstance(agent_value, dict):
                continue
            results = agent_value.get("results", [])
            if not isinstance(results, list):
                continue
            has_violation = False
            for r in results:
                if not isinstance(r, dict) or not r.get("success"):
                    continue
                data = r.get("result", {})
                if not isinstance(data, dict):
                    continue
                for entity_key in ("order", "ticket"):
                    entity = data.get(entity_key, {})
                    if isinstance(entity, dict) and entity.get("user_id"):
                        if entity["user_id"] != session_user_id:
                            has_violation = True
                            break
                if has_violation:
                    break
            if has_violation:
                agent_value["answer"] = (
                    "抱歉，您查询的信息不属于当前账户，无法为您提供。请确认信息是否正确，"
                    "或提供您本人名下的订单号/工单号以便查询。"
                )
                agent_value["results"] = []
        return masked

    def _collect_text(self, sub_results: dict[str, Any]) -> str:
        """汇总所有子 Agent 的输出文本。"""
        parts = []
        for key, value in sub_results.items():
            if isinstance(value, dict):
                # 取 answer 或整个 dict 的字符串表示
                if "answer" in value:
                    parts.append(str(value["answer"]))
                elif "results" in value:
                    parts.append(str(value["results"]))
        return "\n".join(parts)

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        """从 LLM 输出中提取 JSON 对象。

        四层容错策略:
        1. 直接 json.loads 解析
        2. 提取 markdown 代码块 (```json ... ```)
        3. 平衡括号匹配 (从第一个 { 开始, 跟踪嵌套深度找到配对的 })
        4. 贪婪正则兜底
        """
        import json

        # 1. 直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            logger.debug("_parse_json layer 1 (direct) failed: %s, text[:200]=%s", exc, text[:200])

        # 2. 提取 markdown 代码块
        match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError as exc:
                logger.debug("_parse_json layer 2 (markdown block) failed: %s, block[:200]=%s",
                            exc, match.group(1)[:200])

        # 3. 平衡括号匹配: 处理 LLM 在 JSON 前后附加说明文字的情况
        start = text.find('{')
        if start != -1:
            depth = 0
            for i in range(start, len(text)):
                ch = text[i]
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        candidate = text[start:i + 1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError as exc:
                            logger.debug("_parse_json layer 3 (balanced braces) failed: %s, candidate[:200]=%s",
                                        exc, candidate[:200])
                            break  # 括号已平衡但内容非法, 退出内层循环

        # 4. 贪婪正则兜底
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                logger.debug("_parse_json layer 4 (greedy regex) failed: %s, match[:200]=%s",
                            exc, match.group(0)[:200])

        # 全部失败, 返回安全的默认值 (视为合规, 避免误杀正常回复)
        logger.warning("_parse_json: all 4 layers failed, returning default. text[:300]=%s", text[:300])
        return {"compliant": True, "risk_level": "low", "issues": [], "suggestions": []}

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

    @staticmethod
    def _max_risk(current: str, new: str) -> str:
        levels = ["low", "medium", "high", "critical"]
        idx_current = levels.index(current) if current in levels else 0
        idx_new = levels.index(new) if new in levels else 0
        return levels[max(idx_current, idx_new)]
