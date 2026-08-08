#!/usr/bin/env python3
# ============================================================
# user_profile — 用户画像查询脚本
# 从 stdin 读取 JSON，查询用户画像与会员等级（自动脱敏），输出 JSON 到 stdout
# ============================================================
"""
用法（由 skill_runtime 调度）：
    echo '{"user_id":"user-1001"}' | python user_profile.py

输入 JSON 字段：
    user_id (string, required)  用户 ID

输出 JSON：
    { "ok": true, "result": { "found": true, "profile": { ... } } }
        其中 profile 中的姓名、手机号、邮箱已自动脱敏
    { "ok": false, "error": "..." }
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
logger = logging.getLogger("user_profile")


async def main() -> None:
    """主入口：读取 stdin → 查询用户画像 → 输出 JSON。"""
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

    # 3. 查询用户画像（返回的数据已由 UserProfileStore.query 自动脱敏）
    store = UserProfileStore()
    try:
        profile = await store.query(user_id)
        if profile is None:
            output_success({"found": False, "message": f"未找到用户 {user_id}"})
        else:
            output_success(profile)
    except Exception as exc:
        output_error(f"用户画像查询失败: {exc}")


def output_success(result: dict) -> None:
    """输出成功结果到 stdout（skill_runtime 会统一包 {ok, result}）。"""
    print(json.dumps(result, ensure_ascii=False))


def output_error(error: str) -> None:
    """输出错误信息到 stderr，skill_runtime 通过 returncode 判断成败。"""
    print(json.dumps({"ok": False, "error": error}, ensure_ascii=False), file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
