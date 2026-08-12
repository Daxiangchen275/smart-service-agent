# python-impl\evals\test_cases.py
# ============================================================
# 评测用例定义
# ============================================================

# ── 意图分类评测 ──
# 每条: (用户消息, 期望意图, 期望路由agent)
INTENT_CASES: list[tuple[str, str, str]] = [
    # 产品咨询 → knowledge_rag
    ("你们的退货政策是什么？", "consultation", "knowledge_rag"),
    ("iPhone 15 Pro 有什么颜色？", "consultation", "knowledge_rag"),
    ("怎么申请会员升级？", "consultation", "knowledge_rag"),
    ("支付方式有哪些？", "consultation", "knowledge_rag"),

    # 投诉/工单 → ticket_handler
    ("我要投诉物流太慢了", "complaint", "ticket_handler"),
    ("收到的手机屏幕有划痕，我要退款", "complaint", "ticket_handler"),
    ("帮我查一下工单 TKT-20260807-EVAL01 的状态", "complaint", "ticket_handler"),
    ("工单处理到哪一步了？", "complaint", "ticket_handler"),

    # 订单/物流 → tool_executor
    ("查询订单 ORD-20260807-E001 的物流信息", "transaction", "tool_executor"),
    ("我的订单发货了吗？", "transaction", "tool_executor"),
    ("物流单号 SF1234567892 到哪了？", "transaction", "tool_executor"),

    # 账户 → tool_executor
    ("我的账户余额是多少？", "account", "tool_executor"),
    ("帮我查一下会员等级", "account", "tool_executor"),
    ("我的积分还有多少？", "account", "tool_executor"),

    # 未知 → human_handoff
    ("今天天气怎么样？", "unknown", "human_handoff"),
    ("帮我写一首诗", "unknown", "human_handoff"),
]

# ── 实体提取评测 ──
# 每条: (用户消息, 期望实体 {key: value})
ENTITY_CASES: list[tuple[str, dict[str, str]]] = [
    ("查询订单 ORD-20260807-E003 的物流信息",
     {"order_id": "ORD-20260807-E003"}),
    ("工单 TKT-20260807-EVAL01 处理完了吗？",
     {"ticket_id": "TKT-20260807-EVAL01"}),
    ("工单 TKT-20260807-EVAL02 状态",
     {"ticket_id": "TKT-20260807-EVAL02"}),
    ("退款订单 ORD-20260401-A004 对应的工单 TKT-20260403-CDEF12 状态",
     {"order_id": "ORD-20260401-A004", "ticket_id": "TKT-20260403-CDEF12"}),
    ("iPhone 15 Pro 黑色 256G 多少钱？",
     {}),
    ("帮我查查物流 ORD-20260401-A004 到哪了",
     {"order_id": "ORD-20260401-A004"}),
]

# ── RAG 检索评测 ──
# 每条: (用户问题, 期望至少出现在结果中的关键词列表)
RAG_CASES: list[tuple[str, list[str]]] = [
    ("退货政策是什么？",
     ["7天", "无理由", "退货", "退款", "原路返回"]),
    ("会员有什么权益？",
     ["会员", "等级", "银卡", "金卡", "白金卡", "钻石卡", "折扣"]),
    ("怎么投诉？",
     ["投诉", "客服", "工单", "24小时"]),
    ("保修多久？",
     ["保修", "1年", "AppleCare", "电池"]),
    ("支付支持哪些方式？",
     ["微信", "支付宝", "银行卡", "Apple Pay"]),
]

# ── 端到端回复评测 ──
# 每条: (用户消息, 期望回复中至少包含的关键词列表, 禁止出现的词列表)
E2E_CASES: list[tuple[str, list[str], list[str]]] = [
    ("退货政策是什么？",
     ["退货", "7天", "退款"],
     ["不知道", "无法回答"]),
    ("你们支持微信支付吗？",
     ["微信", "支付"],
     ["不支持", "不知道"]),
    ("我要投诉",
     ["投诉", "工单", "客服"],
     []),
    ("帮我查会员等级",
     ["会员", "等级", "银卡", "金卡"],
     []),
]
