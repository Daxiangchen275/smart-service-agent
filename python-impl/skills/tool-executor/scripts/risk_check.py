#!/usr/bin/env python3
# ============================================================
# risk_check — 风控评估脚本
# 从 stdin 读取 JSON，根据用户等级与交易金额评估风险，输出 JSON 到 stdout
# ============================================================
"""
用法（由 skill_runtime 调度）：
    echo '{"user_id":"user-1001","amount":8999.00}' | python risk_check.py

输入 JSON 字段：
    user_id (string, required)  用户 ID
    amount  (number, required)  交易金额（元）

输出 JSON：
    {
      "ok": true,
      "result": {
        "risk_level": "low" | "medium" | "high" | "critical",
        "reasons": ["原因1", "原因2"],
        "allow": true | false,
        "user_level": "gold",
        "amount": 8999.00
      }
    }
"""

from __future__ import annotations

import sys
import os
import json
import asyncio
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from services.stores import UserProfileStore

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("risk_check")


async def main() -> None:
    """主入口：读取 stdin → 风控评估 → 输出 JSON。"""
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

    # 解析 amount，支持字符串和数字
    amount_raw = params.get("amount")
    if amount_raw is None:
        output_error("缺少必填参数: amount")
        return

    try:
        amount = float(amount_raw)
    except (ValueError, TypeError):
        output_error(f"无效的金额: '{amount_raw}'，需要为数字")
        return

    if amount < 0:
        output_error(f"金额不能为负数: {amount}")
        return

    # 3. 执行风控评估
    store = UserProfileStore()
    try:
        result = await store.risk_check(user_id, amount)
        output_success(result)
    except Exception as exc:
        output_error(f"风控评估失败: {exc}")


def output_success(result: dict) -> None:
    """输出成功结果到 stdout（skill_runtime 会统一包 {ok, result}）。"""
    print(json.dumps(result, ensure_ascii=False))


def output_error(error: str) -> None:
    """输出错误信息到 stderr，skill_runtime 通过 returncode 判断成败。"""
    print(json.dumps({"ok": False, "error": error}, ensure_ascii=False), file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
