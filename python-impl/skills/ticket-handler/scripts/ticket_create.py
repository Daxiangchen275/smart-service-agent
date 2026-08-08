#!/usr/bin/env python3
# ============================================================
# ticket_create — 工单创建脚本
# 从 stdin 读取 JSON，调用 TicketStore 创建工单，输出 JSON 到 stdout
# ============================================================
"""
用法（由 skill_runtime 调度）：
    echo '{"user_id":"user-1001","ticket_type":"refund","title":"退款申请","description":"订单 ORD-20260701-A0001 退货","priority":"medium"}' | python ticket_create.py

输入 JSON 字段：
    user_id     (string, required)  用户 ID
    ticket_type (string, required)  工单类型: refund | claim | account_open | account_change | complaint | general
    title       (string, required)  工单标题
    description (string, required)  工单描述
    priority    (string, optional)  优先级: low | medium | high | urgent，默认 medium

输出 JSON：
    { "ok": true, "result": { "created": true, "ticket": { ... } } }
    { "ok": false, "error": "..." }
"""

from __future__ import annotations

import sys
import os
import json
import asyncio
import logging

# 将项目根目录加入 sys.path，确保可以导入项目模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from services.stores import TicketStore

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("ticket_create")


async def main() -> None:
    """主入口：读取 stdin → 创建工单 → 输出 JSON。"""
    # 1. 读取 stdin 中的 JSON 参数
    raw = sys.stdin.read().strip()
    if not raw:
        output_error("未收到任何输入参数")
        return

    try:
        params = json.loads(raw)
    except json.JSONDecodeError as exc:
        output_error(f"输入参数 JSON 解析失败: {exc}")
        return

    # 2. 参数校验
    user_id = str(params.get("user_id", "")).strip()
    if not user_id:
        output_error("缺少必填参数: user_id")
        return

    ticket_type = str(params.get("ticket_type", "")).strip()
    valid_types = {"refund", "claim", "account_open", "account_change", "complaint", "general"}
    if ticket_type not in valid_types:
        output_error(f"无效的工单类型: '{ticket_type}'，有效值为: {', '.join(sorted(valid_types))}")
        return

    title = str(params.get("title", "")).strip()
    if not title:
        output_error("缺少必填参数: title")
        return

    description = str(params.get("description", "")).strip()
    if not description:
        output_error("缺少必填参数: description")
        return

    priority = str(params.get("priority", "medium")).strip()
    valid_priorities = {"low", "medium", "high", "urgent"}
    if priority not in valid_priorities:
        logger.warning("无效的优先级 '%s'，使用默认值 medium", priority)
        priority = "medium"

    # 3. 创建工单
    store = TicketStore()
    try:
        ticket = await store.create(
            user_id=user_id,
            ticket_type=ticket_type,
            title=title,
            description=description,
            priority=priority,
        )
        output_success({"created": True, "ticket": ticket})
    except Exception as exc:
        output_error(f"工单创建失败: {exc}")


def output_success(result: dict) -> None:
    """输出成功结果到 stdout（skill_runtime 会统一包 {ok, result}）。"""
    print(json.dumps(result, ensure_ascii=False))


def output_error(error: str) -> None:
    """输出错误信息到 stderr，skill_runtime 通过 returncode 判断成败。"""
    print(json.dumps({"ok": False, "error": error}, ensure_ascii=False), file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
