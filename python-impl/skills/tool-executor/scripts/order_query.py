#!/usr/bin/env python3
# ============================================================
# order_query — 订单查询脚本
# 从 stdin 读取 JSON，查询订单状态与物流信息，输出 JSON 到 stdout
# ============================================================
"""
用法（由 skill_runtime 调度）：
    echo '{"order_id":"ORD-20260701-A0001"}' | python order_query.py
    echo '{"tracking_no":"SF1234567890"}'   | python order_query.py

输入 JSON 字段：
    order_id    (string, optional)  订单号，如 ORD-20260401-A001
    user_id     (string, optional)  按用户筛选所有进行中订单
    tracking_no (string, optional)  物流单号，如 SF1234567890

输出 JSON：
    { "ok": true, "result": { "found": true, "order": { ... } } }
    { "ok": true, "result": { "found": false, "orders": [...], "count": N } }
    { "ok": false, "error": "..." }
"""

from __future__ import annotations

import sys
import os
import json
import asyncio
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from services.stores import OrderStore, LogisticsStore

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("order_query")


async def main() -> None:
    """主入口：读取 stdin → 查询订单 → 输出 JSON。"""
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

    order_id = str(params.get("order_id", "")).strip()
    user_id = str(params.get("user_id", "")).strip()
    tracking_no = str(params.get("tracking_no", "")).strip()

    if not order_id and not user_id and not tracking_no:
        output_error("至少需要提供 order_id、user_id 或 tracking_no 中的一个参数")
        return

    # 按物流单号查询（走 logistics 表 + JOIN order_summary）
    if tracking_no:
        store = LogisticsStore()
        result = await store.query_by_tracking(tracking_no)
        if result is None:
            output_success({"found": False, "message": f"未找到物流单号 {tracking_no} 的信息"})
        else:
            output_success(result)
        return

    store = OrderStore()

    try:
        # 优先按 order_id 单条查询（store.query 已返回 {found, order} 格式）
        if order_id:
            order = await store.query(order_id, user_id)
            if order is None:
                output_success({"found": False, "message": f"未找到订单 {order_id}"})
            else:
                output_success(order)
        else:
            # 按 user_id 查询进行中订单（SQL 层已过滤终态）
            orders = await store.query_by_user(user_id)
            output_success({
                "found": len(orders) > 0,
                "orders": orders,
                "count": len(orders),
            })
    except Exception as exc:
        output_error(f"订单查询失败: {exc}")


def output_success(result: dict) -> None:
    """输出成功结果到 stdout（skill_runtime 会统一包 {ok, result}）。"""
    print(json.dumps(result, ensure_ascii=False))


def output_error(error: str) -> None:
    """输出错误信息到 stderr，skill_runtime 通过 returncode 判断成败。"""
    print(json.dumps({"ok": False, "error": error}, ensure_ascii=False), file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
