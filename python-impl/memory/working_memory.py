# ============================================================
# 工作记忆 — 请求级进程内记忆（线程安全）
# ============================================================

from __future__ import annotations

import threading
from typing import Any


class WorkingMemory:
    """进程内工作记忆，生命周期对齐单次请求 / 会话。

    特点：
    - 按 session_id 隔离
    - 线程安全（threading.Lock）
    - 滑动窗口，每个 session 最多 50 条记录
    """

    def __init__(self, max_records: int = 50) -> None:
        self._max_records = max_records
        self._store: dict[str, dict[str, Any]] = {}       # session_id → context dict
        self._history: dict[str, list[dict[str, Any]]] = {}  # session_id → list of records
        self._lock = threading.Lock()

    def update(self, session_id: str, data: dict[str, Any]) -> None:
        """追加一条记忆并更新上下文快照。

        Args:
            session_id: 会话 ID
            data: 要追加的键值对
        """
        with self._lock:
            if session_id not in self._store:
                self._store[session_id] = {}
                self._history[session_id] = []

            # 更新上下文快照
            self._store[session_id].update(data)

            # 追加历史记录
            self._history[session_id].append(dict(data))

            # 滑动窗口裁剪
            if len(self._history[session_id]) > self._max_records:
                self._history[session_id] = self._history[session_id][-self._max_records:]

    def get_context(self, session_id: str) -> dict[str, Any]:
        """获取当前 session 的完整上下文字典。"""
        with self._lock:
            return dict(self._store.get(session_id, {}))

    def get_history(self, session_id: str, last_n: int = 10) -> list[dict[str, Any]]:
        """获取最近 N 条历史记录。"""
        with self._lock:
            records = self._history.get(session_id, [])
            return records[-last_n:] if last_n > 0 else []

    def export_for_persistence(self) -> dict[str, Any]:
        """导出所有数据供持久化到短期/长期记忆。"""
        with self._lock:
            return {
                "store": dict(self._store),
                "history": {k: list(v) for k, v in self._history.items()},
            }

    def clear_session(self, session_id: str) -> None:
        """清除指定 session 的所有记忆。"""
        with self._lock:
            self._store.pop(session_id, None)
            self._history.pop(session_id, None)
