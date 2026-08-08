#!/usr/bin/env python3
# ============================================================
# ticket_update — 工单更新脚本
# 从 stdin 读取 JSON，更新工单状态/优先级/描述，输出 JSON 到 stdout
# ============================================================
"""
用法（由 skill_runtime 调度）：
    echo '{"ticket_id":"TK-20260401-ABCDEF","status":"in_progress","priority":"urgent"}' | python ticket_update.py

输入 JSON 字段：
    ticket_id   (string, required)  工单号，如 TK-20260401-ABCDEF
    status      (string, optional)  新状态: open | in_progress | resolved | closed
    priority    (string, optional)  新优先级: low | medium | high | urgent
    description (string, optional)  追加描述内容

输出 JSON：
    { "ok": true, "result": { "updated": true, "ticket": { ... } } }
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
logger = logging.getLogger("ticket_update")


async def main() -> None:
    """主入口：读取 stdin → 更新工单 → 输出 JSON。"""
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
    ticket_id = str(params.get("ticket_id", "")).strip()
    if not ticket_id:
        output_error("缺少必填参数: ticket_id")
        return

    # 校验 status 枚举值
    status = params.get("status")
    if status is not None:
        status = str(status).strip()
        valid_statuses = {"open", "in_progress", "resolved", "closed"}
        if status not in valid_statuses:
            output_error(f"无效的状态: '{status}'，有效值为: {', '.join(sorted(valid_statuses))}")
            return

    # 校验 priority 枚举值
    priority = params.get("priority")
    if priority is not None:
        priority = str(priority).strip()
        valid_priorities = {"low", "medium", "high", "urgent"}
        if priority not in valid_priorities:
            output_error(f"无效的优先级: '{priority}'，有效值为: {', '.join(sorted(valid_priorities))}")
            return

    # 校验 description 是否为字符串
    description = params.get("description")
    if description is not None:
        description = str(description).strip()
        if not description:
            description = None

    # 至少需要一个可更新的字段
    if status is None and priority is None and description is None:
        output_error("至少需要提供 status、priority 或 description 中的一个可更新字段")
        return

    # 3. 执行更新
    store = TicketStore()
    try:
        result = await store.update(
            ticket_id=ticket_id,
            status=status,
            priority=priority,
            description=description,
        )
        if result is None:
            output_success({"found": False, "message": f"未找到工单 {ticket_id}"})
        else:
            output_success(result)
    except Exception as exc:
        output_error(f"工单更新失败: {exc}")


def output_success(result: dict) -> None:
    """输出成功结果到 stdout（skill_runtime 会统一包 {ok, result}）。"""
    print(json.dumps(result, ensure_ascii=False))


def output_error(error: str) -> None:
    """输出错误信息到 stderr，skill_runtime 通过 returncode 判断成败。"""
    print(json.dumps({"ok": False, "error": error}, ensure_ascii=False), file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
