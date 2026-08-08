# ============================================================
# 短期记忆 — Markdown 文件持久化 + 内存热缓存
# ============================================================

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta

# 中国标准时间 UTC+8
CST = timezone(timedelta(hours=8))
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MD_HEADER = """# Session: {session_id}
> Created: {created_at}
> Updated: {updated_at}

"""

_MD_TURN = """## {role} ({timestamp})
{content}

"""


class ShortTermMemory:
    """多轮对话短期记忆。

    - 内存：热数据层，读写 O(1)，关停即丢
    - Markdown：冷持久化层，人类可读，重启不丢失

    默认保留最近 20 轮对话。
    """

    def __init__(self, max_rounds: int = 20,
                 md_dir: str | None = None) -> None:
        self._max_rounds = max_rounds
        self._md_dir: Path | None = Path(md_dir) if md_dir else None
        # 内存热缓存：session_id → [{role, content}, ...]
        self._store: dict[str, list[dict[str, Any]]] = {}

        if self._md_dir is not None:
            self._md_dir.mkdir(parents=True, exist_ok=True)
            logger.info("ShortTermMemory: markdown storage at %s", self._md_dir)

    # ── 写入 ──

    async def add_message(self, session_id: str, role: str, content: str,
                           user_id: str = "default") -> None:
        """追加一条消息，同时写内存和 markdown 文件。

        Args:
            session_id: 会话 ID
            role: 角色 (user / assistant)
            content: 消息内容
            user_id: 用户 ID，用于按用户目录存放会话文件
        """
        msg = {"role": role, "content": content}

        # 内存热写
        if session_id not in self._store:
            self._store[session_id] = []
        self._store[session_id].append(msg)
        if len(self._store[session_id]) > self._max_rounds * 2:
            self._store[session_id] = self._store[session_id][-self._max_rounds * 2:]

        # Markdown 持久化
        if self._md_dir is not None:
            self._write_md(user_id, session_id, msg)

    # ── 读取 ──

    async def get_history(self, session_id: str, last_n: int = 20,
                           user_id: str = "default") -> list[dict[str, Any]]:
        """获取最近 N 条对话消息。

        优先从内存读取；内存缺失且启用了 markdown 时，回退到文件解析。
        """
        records = self._store.get(session_id, [])

        if not records and self._md_dir is not None:
            records = self._read_md(user_id, session_id)
            if records:
                # 回填内存热缓存
                self._store[session_id] = records

        return records[-last_n:] if last_n > 0 else records

    async def get_context_window(self, session_id: str, max_tokens: int = 4000,
                                   user_id: str = "default") -> str:
        """按 token 估算截断的历史文本（中文约 1 字符 ≈ 0.5 token）。"""
        history = await self.get_history(session_id, user_id=user_id)
        lines: list[str] = []
        char_count = 0
        char_limit = max_tokens * 2

        for msg in reversed(history):
            line = f"{msg['role']}: {msg['content']}"
            if char_count + len(line) > char_limit:
                break
            lines.append(line)
            char_count += len(line)

        return "\n".join(reversed(lines))

    # ── 清除 ──

    async def clear(self, session_id: str, user_id: str = "default") -> None:
        """清除 session 的所有记忆（内存 + 文件）。"""
        self._store.pop(session_id, None)

        if self._md_dir is not None:
            md_path = self._md_path(user_id, session_id)
            try:
                md_path.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("Failed to delete markdown %s: %s", md_path, exc)

    # ── Markdown 文件读写 ──

    def _md_path(self, user_id: str, session_id: str) -> Path:
        safe_uid = user_id.replace("/", "_").replace("\\", "_").replace(":", "_")
        safe_sid = session_id.replace("/", "_").replace("\\", "_").replace(":", "_")
        today = datetime.now(CST).strftime("%Y-%m-%d")
        assert self._md_dir is not None
        user_dir = self._md_dir / safe_uid
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir / f"{safe_sid}_{today}.md"

    def _write_md(self, user_id: str, session_id: str, msg: dict[str, Any]) -> None:
        """追加一条消息到 markdown 文件。写入后清理超过 3 天的旧文件。"""
        assert self._md_dir is not None
        self._cleanup_old_files()
        md_path = self._md_path(user_id, session_id)
        now = datetime.now(CST).isoformat()
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        turn = _MD_TURN.format(role=role, timestamp=now, content=content)

        if md_path.exists():
            existing = md_path.read_text(encoding="utf-8")
            lines = existing.split("\n")
            new_lines = []
            updated = False
            for line in lines:
                if line.startswith("> Updated:"):
                    new_lines.append(f"> Updated: {now}")
                    updated = True
                else:
                    new_lines.append(line)
            if not updated:
                new_lines.insert(2, f"> Updated: {now}")
            new_text = "\n".join(new_lines) + turn
        else:
            header = _MD_HEADER.format(session_id=session_id, created_at=now, updated_at=now)
            new_text = header + turn

        try:
            md_path.write_text(new_text, encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to write markdown %s: %s", md_path, exc)

    def _read_md(self, user_id: str, session_id: str) -> list[dict[str, Any]]:
        """从 markdown 文件解析对话记录。会查找最近 3 天的同名文件。"""
        assert self._md_dir is not None
        safe_uid = user_id.replace("/", "_").replace("\\", "_").replace(":", "_")
        safe_sid = session_id.replace("/", "_").replace("\\", "_").replace(":", "_")
        user_dir = self._md_dir / safe_uid

        # 查找最近 3 天内匹配的文件名，取最新的一个
        md_path = None
        if user_dir.exists():
            candidates = sorted(
                user_dir.glob(f"{safe_sid}_*.md"),
                key=lambda p: p.stat().st_mtime, reverse=True,
            )
            if candidates:
                md_path = candidates[0]

        if md_path is None:
            return []

        try:
            text = md_path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to read markdown %s: %s", md_path, exc)
            return []

        records: list[dict[str, Any]] = []
        current_role = ""
        current_lines: list[str] = []

        for line in text.split("\n"):
            if line.startswith("## ") and " (" in line:
                if current_role and current_lines:
                    records.append({
                        "role": current_role,
                        "content": "\n".join(current_lines).strip(),
                    })
                header = line[3:].strip()
                if " (" in header:
                    current_role = header.split(" (")[0].strip()
                current_lines = []
            elif line.startswith("# ") or line.startswith("> "):
                continue
            else:
                current_lines.append(line)

        if current_role and current_lines:
            records.append({
                "role": current_role,
                "content": "\n".join(current_lines).strip(),
            })

        return records

    def _cleanup_old_files(self, keep_days: int = 3) -> None:
        """清理超过指定天数的旧会话文件，避免磁盘堆积。"""
        assert self._md_dir is not None
        cutoff = datetime.now(CST).timestamp() - keep_days * 86400
        try:
            for md_file in self._md_dir.rglob("*.md"):
                if md_file.stat().st_mtime < cutoff:
                    try:
                        md_file.unlink()
                        logger.debug("Cleaned old session file: %s", md_file)
                    except OSError:
                        pass
        except Exception as exc:
            logger.warning("Failed to cleanup old files: %s", exc)
