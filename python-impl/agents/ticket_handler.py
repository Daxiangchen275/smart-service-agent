# ============================================================
# TicketHandler 工单处理 Agent
# ============================================================

from __future__ import annotations

import json
import re
import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

import time as _time

from tracing.otel_config import trace_agent_call
from scripts.skill_runtime import SkillToolSpec, find_tool_by_name, invoke_skill_tool
from tracing.collector import collector

logger = logging.getLogger(__name__)

# ── 常量 ──

TICKET_TYPES = ("refund", "claim", "account_open", "account_change", "complaint", "general")
PRIORITIES   = ("low", "medium", "high", "urgent")
VALID_ACTIONS = ("create", "query", "update")

# 自动优先级映射：工单类型 → 默认优先级
_TYPE_PRIORITY: dict[str, str] = {
    "refund":         "medium",
    "claim":          "high",
    "account_open":   "medium",
    "account_change": "medium",
    "complaint":      "high",
    "general":        "medium",
}

# ── Prompt ──

TICKET_SYSTEM_PROMPT = """你是工单分析助手，负责判断用户意图并提取工单操作参数。只输出 JSON。"""

TICKET_ANALYSIS_PROMPT = """分析以下用户消息，返回工单操作指令。

## 操作类型 (action)
- create : 创建新工单
- query  : 查询已有工单
- update : 更新工单状态或优先级

## 工单类型 (ticket_type)
refund | claim | account_open | account_change | complaint | general

## 优先级 (priority)
low | medium | high | urgent

## 取值原则
- 退款 → ticket_type=refund, priority=medium
- 投诉/不满 → ticket_type=complaint, priority=high
- 开户/注册 → ticket_type=account_open
- 索赔/赔付 → ticket_type=claim, priority=high
- 改资料/绑卡 → ticket_type=account_change
- 金额 > 5000 或有"加急""紧急"字样 → priority=urgent
- 说"多久了""进度""查一下""对应的""是什么""有哪些""查工单""工单号" → action=query
- 说"取消""关闭""改一下" → action=update, status=closed / in_progress
- 说"我要""帮我""申请""创建""提交""反馈" → action=create
- 重要: 用户问"对应/相关/关联的工单"时是在查询已有工单, 不是创建新工单
- 重要: 投诉物流/发货/快递问题但未提具体订单号时，应先去查用户最近的订单再创建工单关联到该订单
- 重要: 如果已知订单号 ORD-xxx，必须在 title 和 description 中包含该订单号
- 重要: 如果用户消息信息严重不足（如只说"我要投诉""我要退款"而无投诉原因、投诉对象、涉及订单），不要创建工单。此时应在 description 开头写 "【信息不足-需追问】"，并列出需要用户补充的信息点。系统会自动拦截此类工单并转为追问回复。

## 输出 JSON 格式
{{
  "action": "create",
  "ticket_type": "refund",
  "title": "简短概括用户诉求（≤25 字）",
  "description": "结构化描述：包含原始诉求、涉及金额/订单号、时间线",
  "priority": "medium",
  "ticket_id": ""
}}

用户消息：{message}
"""


# ── 分析结果 ──

@dataclass
class TicketAnalysis:
    action: str = "create"
    ticket_type: str = "general"
    title: str = ""
    description: str = ""
    priority: str = "medium"
    ticket_id: str = ""

    def to_tool_args(self, user_id: str) -> dict[str, Any]:
        base: dict[str, Any] = {"user_id": user_id}
        if self.action == "create":
            base.update({
                "ticket_type": self.ticket_type,
                "title": self.title,
                "description": self.description,
                "priority": self.priority,
            })
        elif self.action in ("query", "update"):
            base["ticket_id"] = self.ticket_id
        if self.action == "update":
            base["status"] = self.ticket_type  # 语义映射：refund→退票, closed→关闭
        return base


# ── Agent ──

class TicketHandlerAgent:
    """工单处理 Agent：创建 / 查询 / 更新工单。

    工具执行：通过 skill_runtime 从 SKILL.MD 解析工具 → 子进程执行脚本。
    """

    _TICKET_TOOLS = {"ticket_create", "ticket_query", "ticket_update"}

    def __init__(self, llm: BaseChatModel, skill_tools: list[SkillToolSpec],
                 user_id: str = "user-1001",
                 internal_llm: BaseChatModel | None = None) -> None:
        self._llm = llm                          # streaming LLM（用户可见回复）
        self._internal_llm = internal_llm or llm  # internal LLM（工单分析，token 可追踪）
        self._skill_tools = skill_tools          # skill_runtime 解析的工具列表
        self._user_id = user_id

    # ── LangGraph 节点入口 ──

    @trace_agent_call("TicketHandler")
    async def process(self, state: dict[str, Any]) -> dict[str, Any]:
        messages = state.get("messages", [])
        user_id  = state.get("user_id", self._user_id)
        tool_calls = state.get("tool_calls", [])
        self._user_id = user_id

        if not messages:
            return self._empty_result()

        user_text = self._extract_text(messages[-1])

        # 1. 优先消费 IntentRouter 规划的工具
        ticket_results = await self._execute_planned_tools(tool_calls)

        # 2. 无预规划 → LLM 自主分析 + 执行
        if not ticket_results:
            analysis = await self._analyze(user_text)
            # 从 IntentRouter 实体中提取 order_id / ticket_id，供按订单查工单 / 去重使用
            # 实体为空时正则从 user_text 兜底提取
            import re
            intent_entities = state.get("intent_result", {}).get("entities", {})
            order_id = intent_entities.get("order_id", "")
            if not order_id:
                om = re.search(r'ORD-\d{8}-[A-Z0-9]{3,6}', user_text)
                if om:
                    order_id = om.group(0)
            # 优先使用 IntentRouter 上下文改写后提取的 ticket_id（处理"到哪一步了"类追问）
            ticket_id_from_intent = intent_entities.get("ticket_id", "")
            ticket_results = await self._dispatch(analysis, user_id, user_text, order_id, ticket_id_from_intent)

        # 3. LLM 润色回复
        answer = await self._generate_response(user_text, ticket_results)

        existing_sub = state.get("sub_results", {})
        return {
            "sub_results": {
                **existing_sub,
                "ticket_handler": {
                    "agent": "ticket_handler",
                    "results": ticket_results,
                    "answer": answer,
                }
            },
            "current_agent": "ticket_handler",
        }

    # ── 1. 消费预规划工具 ──

    async def _execute_planned_tools(self, tool_calls: list[dict]) -> list[dict]:
        """执行 IntentRouter 规划的工单工具。"""
        results: list[dict] = []
        for tc in tool_calls:
            name = tc.get("name", "")
            if name not in self._TICKET_TOOLS:
                continue
            results.append(await self._call_tool(name, tc.get("arguments", {})))
        return results

    # ── 2. LLM 分析 ──

    async def _analyze(self, user_text: str) -> TicketAnalysis:
        """LLM 分析用户消息 → TicketAnalysis。"""
        try:
            prompt = TICKET_ANALYSIS_PROMPT.format(message=user_text)
            # 内部分析用 internal_llm（非流式），确保 token 统计准确
            response = await self._internal_llm.ainvoke([
                SystemMessage(content=TICKET_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ])
            from tracing.otel_config import capture_llm_tokens
            capture_llm_tokens(response)
            text = self._extract_text(response)
            data = self._parse_json(text)
            if not data:
                raise ValueError(f"LLM returned unparseable response: {text[:200]!r}")
            return self._validate_analysis(data, user_text)
        except Exception as exc:
            logger.warning("LLM ticket analysis failed: %s — using keyword fallback", exc)
            return self._keyword_fallback(user_text)

    async def _dispatch(self, a: TicketAnalysis, user_id: str, user_text: str,
                         order_id: str = "", ticket_id_from_intent: str = "") -> list[dict]:
        """根据分析结果执行对应的 SKILL 工具。

        query 操作无 ticket_id 时的降级链：
        1. 从 user_text 正则提取 ticket_id
        2. 使用 IntentRouter 传递的 ticket_id（上下文改写后提取）
        3. 使用 IntentRouter 提供的 order_id → 调用 TicketStore.query_by_order
        4. 都没有 → 返回友好错误提示

        create 操作去重检查：
        如果提供了 order_id，先查询该订单是否已有工单，避免重复创建。
        """
        tool_name = f"ticket_{a.action}"
        args = a.to_tool_args(user_id)

        # create 时把 order_id 写入工单，确保工单-订单关联
        if a.action == "create" and order_id:
            args["order_id"] = order_id

        # create 操作信息充分性检查：信息严重不足时拒绝创建，先追问用户
        if a.action == "create" and not order_id:
            user_text_stripped = user_text.strip()
            # 情况 1：LLM 按 Prompt 标识了信息不足
            if "信息不足" in a.description:
                return [{
                    "tool": "ticket_create_blocked",
                    "success": False,
                    "result": None,
                    "error": (
                        "用户消息信息不足，无法创建有意义的工单（仅有意图声明，无投诉原因、"
                        "投诉对象、涉及订单号等具体信息）。请礼貌地引导用户补充以下信息：\n"
                        "1. 投诉的具体原因（如商品质量问题、物流延迟、客服态度等）\n"
                        "2. 涉及的订单号（如有）\n"
                        "3. 事发时间及期望的处理方式"
                    ),
                }]
            # 情况 2：LLM 未丰富 description（与原始输入一致）且消息极短 → 信息不足
            if (a.description == user_text_stripped
                    and len(user_text_stripped) <= 15
                    and not self._has_specific_info(user_text_stripped)):
                return [{
                    "tool": "ticket_create_blocked",
                    "success": False,
                    "result": None,
                    "error": (
                        "用户消息信息不足，无法创建有意义的工单（仅有意图声明，无投诉原因、"
                        "投诉对象、涉及订单号等具体信息）。请礼貌地引导用户补充以下信息：\n"
                        "1. 投诉的具体原因（如商品质量问题、物流延迟、客服态度等）\n"
                        "2. 涉及的订单号（如有）\n"
                        "3. 事发时间及期望的处理方式"
                    ),
                }]

        # create 操作无订单号但有具体信息 → 自动匹配用户进行中订单
        if a.action == "create" and not order_id and self._has_specific_info(user_text):
            t0 = _time.perf_counter()
            try:
                from services.stores import OrderStore
                order_store = OrderStore()
                active_orders = await order_store.query_by_user(user_id)
                duration_ms = (_time.perf_counter() - t0) * 1000
                collector.record_tool_call("ticket_order_match", duration_ms, True)

                matched = self._match_order(user_text, active_orders)
                if matched:
                    order_id = matched["order_id"]
                    args["order_id"] = order_id
                    if order_id not in a.title:
                        a.title = f"{a.title}（{order_id}）"
                    if order_id not in a.description:
                        a.description = f"[关联订单: {order_id} 商品: {matched.get('product','')}]\n{a.description}"
                    logger.info("Auto-matched order %s for ticket create", order_id)
            except Exception as exc:
                logger.warning("Order auto-match failed for %s: %s", user_id, exc)

        # 物流/发货投诉：自动匹配后仍无订单号 → 追问用户
        LOGISTICS_KW = ["物流", "快递", "发货", "配送", "运输", "收货", "没收到"]
        if (a.action == "create" and a.ticket_type == "complaint"
                and not order_id
                and any(kw in user_text for kw in LOGISTICS_KW)):
            return [{
                "tool": "ticket_create_blocked",
                "success": False,
                "result": None,
                "error": (
                    "用户投诉物流/配送问题但未提供订单号，且自动匹配未命中。"
                    "请礼貌地请用户提供相关订单号（格式 ORD-YYYYMMDD-XXXXXX），"
                    "以便准确关联工单并加快处理。"
                ),
            }]

        # create 操作去重：按用户+类型查已有工单，标题相似则不重复创建
        if a.action == "create":
            t0 = _time.perf_counter()
            try:
                from services.stores import TicketStore
                store = TicketStore()
                same_type_tickets = await store.query_by_user_and_type(
                    user_id, a.ticket_type,
                )
                duration_ms = (_time.perf_counter() - t0) * 1000
                collector.record_tool_call("ticket_dedup_check", duration_ms, True)

                # 标题相似度检测：bigram Overlap Coefficient
                # 单字拆分问题："手机屏幕有划痕" vs "iPhone屏幕质量问题"
                # → "手"≠"i" "机"≠"P" → 相似度仅 54%。bigram 则捕获
                # "屏幕""申请""退款"等有意义的双字片段，抗措辞差异。
                def _bigrams(text: str) -> set[str]:
                    cleaned = ''.join(c for c in text if c.isalnum() or '一' <= c <= '鿿')
                    if len(cleaned) < 2:
                        return {cleaned}
                    return {cleaned[i:i+2] for i in range(len(cleaned) - 1)}

                title_bg = _bigrams(a.title)
                for t in same_type_tickets:
                    exist_title = t.get("title", "")
                    exist_bg = _bigrams(exist_title)
                    if not title_bg or not exist_bg:
                        continue
                    # Overlap: |交集| / min(|A|,|B|) — 抗长度差异
                    overlap = len(title_bg & exist_bg) / min(len(title_bg), len(exist_bg))
                    if overlap >= 0.25:  # bigram Overlap ≥ 25% → 同类工单
                        return [{
                            "tool": "ticket_dedup_check",
                            "success": True,
                            "result": {
                                "found": True,
                                "ticket": t,
                                "message": (
                                    f"已存在同类工单 {t['ticket_id']}（{exist_title}），"
                                    f"状态: {t.get('status', '?')}，无需重复创建"
                                ),
                            },
                            "error": None,
                        }]
            except Exception as exc:
                logger.warning("Dedup by user+type failed for %s: %s", user_id, exc)

        # query / update 无 ticket_id → 多级兜底
        if a.action in ("query", "update") and not args.get("ticket_id"):
            # 第1级：从当前消息正则提取
            args["ticket_id"] = self._extract_ticket_id(user_text)
        if a.action in ("query", "update") and not args.get("ticket_id") and ticket_id_from_intent:
            # 第2级：IntentRouter 上下文改写后提取的 ticket_id（处理"到哪一步了"类隐式追问）
            args["ticket_id"] = ticket_id_from_intent

        if a.action == "query" and not args.get("ticket_id") and order_id:
            # 按订单号查询关联工单
            t0 = _time.perf_counter()
            try:
                from services.stores import TicketStore
                store = TicketStore()
                tickets = await store.query_by_order(order_id)
                duration_ms = (_time.perf_counter() - t0) * 1000
                collector.record_tool_call("ticket_query_by_order", duration_ms, True)
                if tickets:
                    return [{
                        "tool": "ticket_query_by_order",
                        "success": True,
                        "result": {
                            "found": True,
                            "tickets": tickets,
                            "count": len(tickets),
                            "order_id": order_id,
                        },
                        "error": None,
                    }]
                else:
                    return [{
                        "tool": "ticket_query_by_order",
                        "success": True,
                        "result": {
                            "found": False,
                            "tickets": [],
                            "count": 0,
                            "order_id": order_id,
                            "message": f"未找到订单 {order_id} 关联的工单",
                        },
                        "error": None,
                    }]
            except Exception as exc:
                logger.warning("query_by_order failed for %s: %s", order_id, exc)

        if a.action in ("query", "update") and not args.get("ticket_id"):
            return [{"tool": tool_name, "success": False,
                     "error": "未提供工单号，请提供 TKT- 开头的工单号"}]

        return [await self._call_tool(tool_name, args)]

    # ── 3. 生成回复 ──

    async def _generate_response(self, user_text: str, results: list[dict]) -> str:
        """LLM 将工单执行结果转为自然语言回复。"""
        if not results:
            return "工单处理完成，但未执行任何操作。"

        result_text = json.dumps(results, ensure_ascii=False, indent=2)
        prompt = f"""用户问题：{user_text}

工具执行结果：
{result_text}

请用客服口吻，简洁友好地告知用户操作结果。
- 创建成功 → 告知工单号和预计处理时间
- 查询成功 → 告知当前状态
- 更新成功 → 告知变更内容
- 失败 → 解释原因并给出后续建议"""

        try:
            response = await self._llm.ainvoke([
                SystemMessage(content="你是智能客服助手。"),
                HumanMessage(content=prompt),
            ])
            from tracing.otel_config import capture_llm_tokens
            capture_llm_tokens(response)
            return self._extract_text(response)
        except Exception:
            return self._fallback_format(results)

    # ── 兜底 ──

    def _keyword_fallback(self, user_text: str) -> TicketAnalysis:
        """无 LLM 时的关键词兜底分析。"""
        kw_map = [
            (["投诉", "不满", "差评", "举报"], "complaint"),
            (["退款", "退货", "退费"],          "refund"),
            (["索赔", "赔偿", "赔付"],          "claim"),
            (["开户", "注册", "开通"],          "account_open"),
            (["改", "换绑", "更新资料", "绑卡"], "account_change"),
        ]

        ticket_type = "general"
        for keywords, ttype in kw_map:
            if any(kw in user_text for kw in keywords):
                ticket_type = ttype
                break

        action = "create"
        if any(kw in user_text for kw in ["查", "进度", "状态", "多久", "对应", "相关", "关联", "是什么", "有哪些", "多少", "工单号"]):
            action = "query"
        elif any(kw in user_text for kw in ["取消", "关闭", "加急", "催"]):
            action = "update"

        priority = "urgent" if any(kw in user_text for kw in ["加急", "紧急", "马上", "立刻"]) else \
                   _TYPE_PRIORITY.get(ticket_type, "medium")

        return TicketAnalysis(
            action=action,
            ticket_type=ticket_type,
            title=user_text[:25].strip(),
            description=user_text,
            priority=priority,
            ticket_id=self._extract_ticket_id(user_text),
        )

    def _fallback_format(self, results: list[dict]) -> str:
        """纯规则格式化（LLM 不可用时）。"""
        parts: list[str] = []
        for r in results:
            if not r.get("success"):
                parts.append(f"操作失败：{r.get('error', '未知错误')}")
                continue
            data = r.get("result", {})
            tool = r.get("tool", "")
            ticket = data.get("ticket", {})
            tid = ticket.get("ticket_id", "")

            if data.get("tickets") is not None:
                # 按订单查询返回的工单列表
                tickets_list = data.get("tickets", [])
                order_id = data.get("order_id", "")
                if not tickets_list:
                    parts.append(f"未找到订单 {order_id} 关联的工单。")
                else:
                    lines = [f"订单 {order_id} 关联的工单（共 {len(tickets_list)} 个）："]
                    for t in tickets_list:
                        lines.append(
                            f"  - {t.get('ticket_id','')}: {t.get('title','')} "
                            f"[{t.get('status','')}, {t.get('priority','')}]"
                        )
                    parts.append("\n".join(lines))
            elif "create" in tool:
                parts.append(f"工单已创建：{tid}\n类型：{ticket.get('ticket_type','')}，优先级：{ticket.get('priority','')}\n我们将在 24 小时内处理，请耐心等待。")
            elif "query" in tool:
                parts.append(f"工单 {tid} 当前状态：{ticket.get('status','')}，优先级：{ticket.get('priority','')}")
            elif "update" in tool:
                parts.append(f"工单 {tid} 已更新，当前状态：{ticket.get('status','')}")
            else:
                parts.append(str(data))
        return "\n\n".join(parts) if parts else "查询完成，但未获取到有效数据。"

    # ── 工具执行（skill_runtime 子进程调用）──

    async def _call_tool(self, name: str, args: dict) -> dict[str, Any]:
        """通过 skill_runtime 查找并执行 SKILL.MD 中定义的脚本工具。

        返回统一格式：
            {"tool": str, "success": bool, "result": Any, "error": str|None}
        """
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
                return {
                    "tool": name,
                    "success": True,
                    "result": raw.get("result", {}),
                    "error": None,
                }
            else:
                return {
                    "tool": name,
                    "success": False,
                    "result": None,
                    "error": raw.get("error", "skill_runtime 执行失败"),
                }
        except Exception as exc:
            duration_ms = (_time.perf_counter() - t0) * 1000
            logger.warning("skill_runtime '%s' failed: %s", name, exc)
            collector.record_tool_call(name, duration_ms, False)
            return {"tool": name, "success": False, "error": str(exc)}

    @staticmethod
    def _extract_ticket_id(text: str) -> str:
        m = re.search(r'TKT?-\d{8}-[A-Z0-9]{4,6}', text)
        return m.group(0) if m else ""

    @staticmethod
    def _has_specific_info(text: str) -> bool:
        """检查消息是否包含具体信息（非纯意图声明）。

        包含以下任一要素即视为有具体信息：
        - 订单号 (ORF-xxx)
        - 工单号 (TKT-xxx)
        - 金额数字
        - 具体商品/品牌名
        - 具体问题描述关键词
        - 时间表达
        """
        if re.search(r'ORD-\d{8}-[A-Z0-9]{3,6}', text):
            return True
        if re.search(r'TKT?-\d{8}-[A-Z0-9]{4,6}', text):
            return True
        if re.search(r'\d+\s*(?:元|块|万|千)', text):
            return True
        # 具体问题关键词（非纯"我要投诉/退款"类意图声明）
        if re.search(r'屏幕|坏|碎|划痕|裂|掉色|褪色|开胶|进水|不工作|死机|卡顿|闪退|无法|不能'
                     r'|发错|少发|漏发|破损|包装|态度|辱骂|欺骗|虚假|假货|仿冒'
                     r'|延迟|慢|还没到|没收到|丢件|冒领',
                     text):
            return True
        # 时间表达
        if re.search(r'昨天|今天|前天|上[周月]|星期|周[一二三四五六日]'
                     r'|\d+[月号日天]|\d+小时|\d+分钟',
                     text):
            return True
        return False

    @staticmethod
    def _match_order(user_text: str, orders: list[dict]) -> dict | None:
        """从用户进行中订单中匹配最可能相关的订单。

        匹配策略：
        1. 先检查 user_text 是否已包含订单号 → 直接匹配
        2. 关键词匹配：user_text 中的商品词汇 vs 订单商品名
        3. 返回得分最高的订单（需超过阈值），否则返回 None
        """
        if not orders:
            return None

        # 策略 1：消息中已含订单号
        om = re.search(r'ORD-\d{8}-[A-Z0-9]{3,6}', user_text)
        if om:
            oid = om.group(0)
            for o in orders:
                if o["order_id"] == oid:
                    return o

        # 策略 2：商品关键词匹配
        _PRODUCT_KW = {
            "手机": ["手机", "iPhone", "Mate", "Galaxy", "S25", "小米", "OPPO", "vivo"],
            "耳机": ["耳机", "AirPods", "Sony", "WH-1000", "降噪"],
            "笔记本": ["笔记本", "电脑", "MacBook", "XPS", "ThinkPad"],
            "平板": ["平板", "iPad", "Pad"],
            "手表": ["手表", "Watch", "腕表", "手环"],
            "配件": ["充电", "数据线", "保护壳", "贴膜", "支架"],
        }

        scored = []
        for o in orders:
            product = o.get("product", "")
            order_text = f"{product} {o['order_id']}"
            score = 0.0

            for _category, kws in _PRODUCT_KW.items():
                for kw in kws:
                    if kw.lower() in user_text.lower() and kw.lower() in order_text.lower():
                        score += 2.0  # 双向命中
                    elif kw.lower() in user_text.lower():
                        score += 0.5  # 单向命中

            # 状态加权：shipped > paid > pending
            status = o.get("status", "")
            if status == "shipped":
                score += 1.0
            elif status == "paid":
                score += 0.8
            elif status == "pending":
                score += 0.3

            if score > 0:
                scored.append((score, o))

        scored.sort(key=lambda x: x[0], reverse=True)

        if scored and scored[0][0] >= 2.0:
            best = scored[0][1]
            logger.info("_match_order: matched %s (product=%s, score=%.1f)",
                        best["order_id"], best.get("product", "?"), scored[0][0])
            return best

        return None

    @staticmethod
    def _extract_text(msg: Any) -> str:
        return msg.content if hasattr(msg, "content") else str(msg)

    # ── JSON 解析 ──

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        """从 LLM 输出中提取 JSON 对象，四层容错。"""
        # 1. 直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            logger.debug("TicketHandler _parse_json layer 1 (direct) failed: %s, text[:200]=%s", exc, text[:200])

        # 2. 提取 markdown 代码块
        match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError as exc:
                logger.debug("TicketHandler _parse_json layer 2 (markdown block) failed: %s, block[:200]=%s",
                            exc, match.group(1)[:200])

        # 3. 平衡括号匹配
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
                            logger.debug("TicketHandler _parse_json layer 3 (balanced braces) failed: %s, candidate[:200]=%s",
                                        exc, candidate[:200])
                            break

        # 4. 贪婪正则兜底
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                logger.debug("TicketHandler _parse_json layer 4 (greedy regex) failed: %s, match[:200]=%s",
                            exc, match.group(0)[:200])

        logger.warning("TicketHandler _parse_json: all 4 layers failed, returning {}. text[:300]=%s", text[:300])
        return {}

    def _validate_analysis(self, data: dict, user_text: str) -> TicketAnalysis:
        """校验 LLM 输出，非法值用兜底替换。"""
        action = data.get("action", "create")
        if action not in VALID_ACTIONS:
            action = "create"

        ticket_type = data.get("ticket_type", "general")
        if ticket_type not in TICKET_TYPES:
            ticket_type = "general"

        priority = data.get("priority", _TYPE_PRIORITY.get(ticket_type, "medium"))
        if priority not in PRIORITIES:
            priority = "medium"

        return TicketAnalysis(
            action=action,
            ticket_type=ticket_type,
            title=data.get("title", user_text[:25]).strip()[:25],
            description=data.get("description", user_text),
            priority=priority,
            ticket_id=data.get("ticket_id", ""),
        )

    def _empty_result(self) -> dict[str, Any]:
        return {
            "sub_results": {"ticket_handler": {"agent": "ticket_handler", "answer": ""}},
            "current_agent": "ticket_handler",
        }
