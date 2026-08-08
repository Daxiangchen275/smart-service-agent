# ============================================================
# IntentRouter 意图路由 Agent — 纯意图分类，不做工具规划
# ============================================================

from __future__ import annotations

import json
import re
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from tracing.otel_config import trace_agent_call
from tracing.collector import collector

logger = logging.getLogger(__name__)


# ── 意图分类 ──

class IntentCategory(str, Enum):
    consultation = "consultation"    # 产品/政策咨询 → knowledge_rag
    complaint = "complaint"          # 投诉/工单 → ticket_handler
    transaction = "transaction"      # 订单/物流查询 → tool_executor
    account = "account"              # 账户/会员 → tool_executor
    unknown = "unknown"              # 未知 → human_handoff


# ── 意图结果 ──

@dataclass
class IntentResult:
    primary_intent: IntentCategory
    secondary_intent: str = ""
    confidence: float = 0.0
    reasoning: str = ""
    entities: dict[str, str] = field(default_factory=dict)
    suggested_agent: str = "human_handoff"


# ── 意图路由 Agent ──

INTENT_SYSTEM_PROMPT = """你是智能客服的意图识别模块。分析用户消息，输出 JSON 格式的意图分析结果。

## 意图类别
- consultation: 产品/政策/流程咨询
- complaint: 投诉/退款/工单相关（包括查询工单、工单状态、创建工单）
- transaction: 订单查询/物流（仅限查订单本身，不含查订单关联的工单）
- account: 查询个人账户余额/积分/等级（仅限查"我"的数据）
- unknown: 无法识别

## 路由规则
- consultation → suggested_agent: "knowledge_rag"
- complaint → suggested_agent: "ticket_handler"
- transaction → suggested_agent: "tool_executor"
- account → suggested_agent: "tool_executor"
- unknown → suggested_agent: "human_handoff"

## 关键区分
- "会员有什么权益/有哪些等级/怎么升级/会员折扣" → consultation（政策咨询，走知识库）
- "我的账户余额/我的积分/我的等级/个人信息" → account（查个人数据，走工具查询）
- "查询订单的物流/状态/金额" → transaction（查订单本身）
- "怎么投诉/如何退款/退货流程/投诉渠道" → consultation（问流程/政策，走知识库）
- "我要投诉XX/帮我退款/订单坏了" → complaint（要实际操作，走工单）
- "查询订单对应的工单/关联工单" → complaint（查工单，不是查订单）

## 输出格式 (严格 JSON)
{
  "primary_intent": "consultation",
  "secondary_intent": "退货政策",
  "confidence": 0.92,
  "reasoning": "用户询问退货政策，属于产品政策咨询，应走知识库检索",
  "entities": {"order_id": "ORD-xxx", "ticket_id": "TKT-xxx"},
  "suggested_agent": "knowledge_rag"
}
"""

CONTEXT_REWRITE_PROMPT = """你是对话上下文改写助手。当前消息含指代词，语义不完整。

任务：参考对话历史消解指代，输出一句完整独立的查询。

规则：
1. 必须将指代词替换为下方「已知实体」中的具体值（订单号、工单号）
2. 保持用户原始意图和疑问词不变
3. 只输出改写后的一句话，不加引号、解释或前缀

## 已知实体（必须使用，不要遗漏）
__ENTITIES__

## 对话历史
__HISTORY__

## 当前消息
__QUESTION__

改写结果："""


class IntentRouterAgent:
    """意图路由 Agent：识别用户意图、提取实体、路由到对应子 Agent。

    职责：
    - 上下文改写：短追问（"对应的工单是什么？"）补全历史实体
    - LLM 分类 + 实体提取（主引擎）
    - 关键词正则兜底（LLM 不可用时）
    - 不做工具规划（工具选择由下游 Agent 根据 SKILL.MD 自行决策）
    """

    # 短追问模式：当前消息简短且含指代词，需要从历史补全上下文
    # 匹配规则：消息中出现以下任一指代词
    #   - 指示: 这个、那个、该、此、本、上述、前面、刚才、上面、以下、以上
    #   - 人称: 它、他、她、其
    #   - 关联: 对应的、相关的、关联的、前者、后者
    #   - 省略: 怎么样、呢
    #   - 进度追问: 哪一步、到哪了、什么进度、什么情况、处理得
    _SHORT_FOLLOWUP_RE = re.compile(
        r'(对应的|相关的|关联的|这个|那个|它的|他的|她的|该|上述|前面|刚才'
        r'|上面|以下|以上|此|本|前者|后者|它|他|她|其|怎么样|呢'
        r'|哪一步|到哪了|什么进度|什么情况|处理得|如何了|怎样了|好了吗'
        r'|还在|还没|到了吗|有结果吗|有回复吗)'
    )

    def __init__(self, llm: BaseChatModel, user_id: str = "user-1001") -> None:
        self._llm = llm
        self._user_id = user_id

    @trace_agent_call("IntentRouter")
    async def process(self, state: dict[str, Any]) -> dict[str, Any]:
        """LangGraph 节点入口。"""
        messages = state.get("messages", [])
        user_id = state.get("user_id", self._user_id)
        self._user_id = user_id

        if not messages:
            return self._empty_result()

        last_msg = messages[-1]
        user_text = last_msg.content if hasattr(last_msg, "content") else str(last_msg)
        original_text = user_text

        # 0. 上下文改写：LLM 消解指代，将短追问补全为完整独立查询
        user_text = await self._expand_context(user_text, messages)

        # 1. LLM 分类
        intent_result = await self.classify(user_text)

        # 写入监控指标
        collector.record_intent(
            primary=intent_result.primary_intent.value,
            agent=intent_result.suggested_agent,
            confidence=intent_result.confidence,
            rewrite_occurred=(user_text != original_text),
            is_handoff=(intent_result.suggested_agent == "human_handoff"),
        )

        # 1.5 实体补全：仅当当前消息是短追问（含指代词）时，才从历史消息中继承实体。
        #     避免独立的新问题（如"获取当前用户信息"）错误继承上一轮的 order_id。
        if not intent_result.entities and len(messages) >= 2:
            has_referent = bool(self._SHORT_FOLLOWUP_RE.search(user_text.strip()))
            if has_referent:
                for msg in reversed(messages[-6:-1]):
                    content = msg.content if hasattr(msg, "content") else str(msg)
                    for eid, evalue in self._extract_entities(content).items():
                        if eid not in intent_result.entities:
                            intent_result.entities[eid] = evalue
                    if len(intent_result.entities) >= 2:
                        break

        # 2. 写入状态（手动合并 sub_results，避免覆盖同轮其他 Agent 的输出）
        existing_sub = state.get("sub_results", {})
        return {
            "intent": intent_result.suggested_agent,
            "intent_result": {
                "primary_intent": intent_result.primary_intent.value,
                "secondary_intent": intent_result.secondary_intent,
                "confidence": intent_result.confidence,
                "reasoning": intent_result.reasoning,
                "entities": intent_result.entities,
                "suggested_agent": intent_result.suggested_agent,
            },
            "tool_calls": [],  # 不再由 IntentRouter 规划，下游 Agent 自行决策
            "sub_results": {
                **existing_sub,
                "intent_router": {
                    "agent": "intent_router",
                    "intent": intent_result.suggested_agent,
                    "confidence": intent_result.confidence,
                    "raw_input": original_text,
                    "rewritten_input": user_text,
                }
            },
            "current_agent": "intent_router",
        }

    async def classify(self, user_message: str) -> IntentResult:
        """调用 LLM 返回 JSON 结构化意图结果。
        LLM 调用成功但 JSON 解析全部失败时，也会降级到关键词 fallback。
        """
        try:
            response = await self._llm.ainvoke([
                SystemMessage(content=INTENT_SYSTEM_PROMPT),
                HumanMessage(content=user_message),
            ])
            text = self._extract_text(response)

            # 诊断日志 + Token 手动上报（兜底 callback handler 未捕获的情况）
            from tracing.otel_config import capture_llm_tokens
            capture_llm_tokens(response)
            resp_meta = getattr(response, "response_metadata", {}) or {}
            usage_meta = getattr(response, "usage_metadata", {}) or {}
            finish = resp_meta.get("finish_reason", "?")
            model = resp_meta.get("model_name", "?")
            usage = resp_meta.get("token_usage", {}) or usage_meta
            logger.info("IntentRouter LLM response: model=%s finish=%s tokens=%s content_len=%d",
                        model, finish, usage, len(text))

            data = self._parse_json(text)
            # _parse_json 四层全失败时返回 {}，此时应走 fallback
            if not data:
                raise ValueError(
                    f"LLM returned unparseable response: finish={finish} "
                    f"text[:200]={text[:200]!r}"
                )
            return self._build_result(data)
        except Exception as exc:
            logger.warning("IntentRouter LLM classify failed: %s — using fallback", exc)
            return self._fallback_classify(user_message)

    def _parse_json(self, text: str) -> dict[str, Any]:
        """从 LLM 输出中提取 JSON。三层容错：直接解析 → 代码块 → 正则提取。"""
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            logger.debug("_parse_json layer 1 (direct) failed: %s, text[:200]=%s", exc, text[:200])

        match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError as exc:
                logger.debug("_parse_json layer 2 (markdown block) failed: %s, block[:200]=%s",
                            exc, match.group(1)[:200])

        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                logger.debug("_parse_json layer 3 (greedy regex) failed: %s, match[:200]=%s",
                            exc, match.group(0)[:200])

        logger.warning("_parse_json: all 3 layers failed, returning {}. text[:300]=%s", text[:300])
        return {}

    def _build_result(self, data: dict[str, Any]) -> IntentResult:
        """从 LLM 返回的 dict 构建 IntentResult。"""
        primary = data.get("primary_intent", "unknown")
        try:
            primary_intent = IntentCategory(primary)
        except ValueError:
            primary_intent = IntentCategory.unknown

        return IntentResult(
            primary_intent=primary_intent,
            secondary_intent=data.get("secondary_intent", ""),
            confidence=float(data.get("confidence", 0.5)),
            reasoning=data.get("reasoning", ""),
            entities=data.get("entities", {}),
            suggested_agent=data.get("suggested_agent", "human_handoff"),
        )

    def _fallback_classify(self, user_message: str) -> IntentResult:
        """LLM 解析失败时的关键词正则兜底。"""
        entities = self._extract_entities(user_message)

        # 投诉/退款关键词 → ticket_handler
        # "怎么/如何 + 投诉/退款/退货" → 问流程，走知识库
        _how_keywords = ["怎么", "如何", "怎样", "怎么样", "流程", "渠道", "方式"]
        if any(kw in user_message for kw in _how_keywords) and \
           any(kw in user_message for kw in ["投诉", "退款", "退货"]):
            return IntentResult(
                primary_intent=IntentCategory.consultation,
                secondary_intent="流程咨询",
                confidence=0.75,
                entities=entities,
                suggested_agent="knowledge_rag",
            )

        complaint_keywords = ["投诉", "退款", "退货", "不满", "差评", "赔偿", "开户", "办理", "工单"]
        if any(kw in user_message for kw in complaint_keywords):
            return IntentResult(
                primary_intent=IntentCategory.complaint,
                secondary_intent="业务办理",
                confidence=0.7,
                entities=entities,
                suggested_agent="ticket_handler",
            )

        # 订单关键词 → tool_executor
        order_keywords = ["订单", "物流", "快递", "发货", "收货", "order"]
        if any(kw in user_message for kw in order_keywords) or "order_id" in entities:
            return IntentResult(
                primary_intent=IntentCategory.transaction,
                secondary_intent="订单查询",
                confidence=0.7,
                entities=entities,
                suggested_agent="tool_executor",
            )

        # 会员权益/政策咨询 → knowledge_rag（不是查个人数据，是问规则）
        if "会员" in user_message and any(kw in user_message for kw in
                                          ["权益", "折扣", "有什么", "有哪些", "怎么升级", "规则"]):
            return IntentResult(
                primary_intent=IntentCategory.consultation,
                secondary_intent="会员权益咨询",
                confidence=0.75,
                entities=entities,
                suggested_agent="knowledge_rag",
            )

        # 账户关键词 → tool_executor（查"我的"余额/积分/等级）
        account_keywords = ["我的账户", "余额", "积分", "个人信息"]
        if any(kw in user_message for kw in account_keywords) or (
           "会员" in user_message and not any(kw in user_message for kw in
                                              ["权益", "有什么", "有哪些", "怎么", "规则"])):
            return IntentResult(
                primary_intent=IntentCategory.account,
                secondary_intent="账户查询",
                confidence=0.7,
                entities=entities,
                suggested_agent="tool_executor",
            )

        # 默认 → human_handoff（无法识别意图时转人工，避免胡乱回答）
        return IntentResult(
            primary_intent=IntentCategory.unknown,
            secondary_intent="意图不明",
            confidence=0.5,
            entities=entities,
            suggested_agent="human_handoff",
        )

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
    def _extract_entities(text: str) -> dict[str, str]:
        """正则提取订单号和工单号。"""
        entities: dict[str, str] = {}
        order_match = re.search(r'ORD-\d{8}-[A-Z0-9]{3,6}', text)
        if order_match:
            entities["order_id"] = order_match.group(0)
        ticket_match = re.search(r'TKT?-\d{8}-[A-Z0-9]{4,6}', text)
        if ticket_match:
            entities["ticket_id"] = ticket_match.group(0)
        return entities

    # 消息已含显式 ID（订单号/工单号/物流单号）→ 语义完整，无需改写
    _SELF_CONTAINED_RE = re.compile(
        r'ORD-\d{8}-[A-Z0-9]{3,6}'
        r'|TKT?-\d{8}-[A-Z0-9]{4,6}'
        r'|(?:SF|YT|ZTO|JD|YTO|HTKY|STO)\d{6,20}'
    )

    async def _expand_context(self, user_text: str, messages: list) -> str:
        """LLM 上下文改写：消解短追问中的指代词，输出完整独立查询。

        流程：
        1. 正则从历史消息中提取订单号/工单号作为「已知实体」
        2. 将已知实体注入 LLM prompt，确保改写结果必然包含这些 ID
        3. LLM 消解指代，输出完整查询

        不触发：消息已含显式 ID（ORF-xxx / TKT-xxx / SFxxx）→ 语义完整，直接跳过
        """
        msg_count = len(messages)
        has_referent = bool(self._SHORT_FOLLOWUP_RE.search(user_text.strip()))

        if msg_count < 2:
            logger.info("Context rewrite SKIP: only %d message(s) in conversation, need >= 2", msg_count)
            return user_text
        if not has_referent:
            logger.info("Context rewrite SKIP: no referent word found in [%s]", user_text)
            return user_text
        if self._SELF_CONTAINED_RE.search(user_text):
            logger.info("Context rewrite SKIP: message already contains explicit ID, self-contained")
            return user_text

        logger.warning("Context rewrite TRIGGERED: msg_count=%d, text=[%s]", msg_count, user_text)

        # 从历史消息中正则提取已知实体（不依赖 LLM，确保不遗漏）
        known_entities: dict[str, str] = {}
        history_parts: list[str] = []
        for msg in messages[-6:-1]:  # 排除最后一条（当前消息）
            content = msg.content if hasattr(msg, "content") else str(msg)
            msg_type = getattr(msg, "type", "human")
            role = "用户" if msg_type == "human" else "客服"
            history_parts.append(f"{role}: {content}")

            # 从每条历史消息中提取实体
            for eid, evalue in self._extract_entities(content).items():
                if eid not in known_entities:
                    known_entities[eid] = evalue

        if not history_parts:
            return user_text

        # 构建实体提示
        if known_entities:
            entity_lines = [f"- {k}: {v}" for k, v in known_entities.items()]
            entity_text = "\n".join(entity_lines)
        else:
            entity_text = "（无已知实体，请根据对话历史推断）"

        history_text = "\n".join(history_parts)

        try:
            # 用 replace 而非 format：对话历史可能含 { } 导致 format 抛 KeyError
            prompt = (CONTEXT_REWRITE_PROMPT
                      .replace("__ENTITIES__", entity_text)
                      .replace("__HISTORY__", history_text)
                      .replace("__QUESTION__", user_text))
            response = await self._llm.ainvoke([HumanMessage(content=prompt)])
            from tracing.otel_config import capture_llm_tokens
            capture_llm_tokens(response)
            rewritten = self._extract_text(response).strip()

            if rewritten and rewritten != user_text:
                logger.warning("Context rewrite LLM success: [%s] -> [%s]", user_text, rewritten)
                return rewritten

            # LLM 未改写或输出相同 → 正则兜底：直接将已知实体拼接到查询前
            if known_entities:
                entity_ids = [v for v in known_entities.values()]
                fallback = f"查询{'、'.join(entity_ids)} {user_text}"
                logger.warning("Context rewrite: LLM returned same text, using regex fallback. [%s] -> [%s]", user_text, fallback)
                return fallback

            logger.warning("Context rewrite: LLM returned same text and no known_entities, returning original")
        except Exception as exc:
            logger.warning("Context rewrite LLM call failed: %s, returning original text [%s]", exc, user_text)

        logger.info("Context rewrite: returning unchanged text [%s]", user_text)
        return user_text

    def _empty_result(self) -> dict[str, Any]:
        return {
            "intent": "human_handoff",
            "intent_result": {},
            "tool_calls": [],
            "sub_results": {},
            "current_agent": "intent_router",
        }
