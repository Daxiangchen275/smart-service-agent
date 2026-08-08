#!/usr/bin/env python3
# ============================================================
# ticket_query — 工单查询脚本
# 从 stdin 读取 JSON，查询工单状态，输出 JSON 到 stdout
# ============================================================
"""
用法（由 skill_runtime 调度）：
    echo '{"ticket_id":"TK-20260401-ABCDEF"}' | python ticket_query.py

输入 JSON 字段：
    ticket_id (string, optional)  工单号，如 TK-20260401-ABCDEF
    user_id   (string, optional)  按用户筛选所有工单

输出 JSON：
    { "ok": true, "result": { "found": true, "ticket": { ... } } }
    { "ok": true, "result": { "found": false, "tickets": [...], "count": N } }
    { "ok": false, "error": "..." }
"""

from __future__ import annotations

import sys
import os
import json
import asyncio
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from services.stores import TicketStore

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("ticket_query")


async def main() -> None:
    """主入口：读取 stdin → 查询工单 → 输出 JSON。"""
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

    ticket_id = str(params.get("ticket_id", "")).strip()
    user_id = str(params.get("user_id", "")).strip()

    if not ticket_id and not user_id:
        output_error("至少需要提供 ticket_id 或 user_id 中的一个参数")
        return

    store = TicketStore()

    try:
        # 优先按 ticket_id 单条查询
        if ticket_id:
            ticket = await store.query(ticket_id, user_id)
            if ticket is None:
                output_success({"found": False, "message": f"未找到工单 {ticket_id}"})
            else:
                output_success(ticket)
        else:
            # 按 user_id 查询全部工单
            tickets = await store.query_by_user(user_id)
            output_success({
                "found": len(tickets) > 0,
                "tickets": tickets,
                "count": len(tickets),
            })
    except Exception as exc:
        output_error(f"工单查询失败: {exc}")


def output_success(result: dict) -> None:
    """输出成功结果到 stdout（skill_runtime 会统一包 {ok, result}）。"""
    print(json.dumps(result, ensure_ascii=False))


def output_error(error: str) -> None:
    """输出错误信息到 stderr，skill_runtime 通过 returncode 判断成败。"""
    print(json.dumps({"ok": False, "error": error}, ensure_ascii=False), file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
