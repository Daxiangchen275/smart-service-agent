from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_FRONTMATTER_RE = re.compile(r"^\s*---\s*\n([\s\S]*?)\n---\s*", re.MULTILINE)
_TOOL_INTENT_RE = re.compile(r"^\s*-\s*intent:\s*(.+?)\s*$")
_TOOL_SCRIPT_RE = re.compile(r"^\s*script:\s*(.+?)\s*$")
_TOOL_TRIGGERS_RE = re.compile(r"^\s*triggers:\s*(.+?)\s*$")


@dataclass(frozen=True)
class SkillToolSpec:
    intent: str
    script_path: Path
    triggers: tuple[str, ...]
    name: str
    description: str


def _normalize_text(value: str) -> str:
    return value.strip().lower()


def _extract_frontmatter(skill_md_text: str) -> dict[str, str]:
    match = _FRONTMATTER_RE.search(skill_md_text)
    if not match:
        return {}
    body = match.group(1)
    out: dict[str, str] = {}
    for line in body.splitlines():
        item = line.strip()
        if not item or ":" not in item:
            continue
        key, val = item.split(":", 1)
        out[key.strip()] = val.strip()
    return out


def _extract_tool_defs(skill_md_text: str) -> list[dict[str, Any]]:
    tool_defs: list[dict[str, Any]] = []
    lines = skill_md_text.splitlines()
    i = 0
    while i < len(lines):
        intent_m = _TOOL_INTENT_RE.match(lines[i])
        if not intent_m:
            i += 1
            continue
        item: dict[str, Any] = {"intent": intent_m.group(1).strip()}
        i += 1
        while i < len(lines):
            if _TOOL_INTENT_RE.match(lines[i]):
                break
            script_m = _TOOL_SCRIPT_RE.match(lines[i])
            if script_m:
                item["script"] = script_m.group(1).strip()
            triggers_m = _TOOL_TRIGGERS_RE.match(lines[i])
            if triggers_m:
                raw = triggers_m.group(1).strip()
                item["triggers"] = [v.strip() for v in raw.split(",") if v.strip()]
            i += 1
        tool_defs.append(item)
    return tool_defs


def load_skill_tools(skills_root: str | Path) -> list[SkillToolSpec]:
    root = Path(skills_root)
    if not root.exists() or not root.is_dir():
        return []

    tools: list[SkillToolSpec] = []
    for skill_md in root.glob("*/SKILL.md"):
        skill_dir = skill_md.parent
        text = skill_md.read_text(encoding="utf-8")
        meta = _extract_frontmatter(text)
        skill_name = str(meta.get("name") or skill_dir.name)
        skill_desc = str(meta.get("description") or "")
        tool_defs = _extract_tool_defs(text)
        if not tool_defs:
            continue
        for item in tool_defs:
            if not isinstance(item, dict):
                continue
            intent = _normalize_text(str(item.get("intent") or ""))
            script = str(item.get("script") or "").strip()
            if not intent or not script:
                continue
            script_path = (skill_dir / script).resolve()
            if not script_path.exists():
                continue
            triggers_raw = item.get("triggers") or []
            triggers: list[str] = []
            if isinstance(triggers_raw, list):
                triggers.extend(str(t).strip() for t in triggers_raw if str(t).strip())
            triggers.append(intent)
            tools.append(
                SkillToolSpec(
                    intent=intent,
                    script_path=script_path,
                    triggers=tuple(_normalize_text(t) for t in triggers),
                    name=skill_name,
                    description=skill_desc,
                )
            )
    return tools


def find_tool_by_name(name: str, tools: list[SkillToolSpec]) -> SkillToolSpec | None:
    """根据工具名（即 intent 字段）查找 SkillToolSpec。

    Args:
        name: 工具名，如 ticket_create、order_query
        tools: load_skill_tools() 返回的工具列表

    Returns:
        匹配的 SkillToolSpec，未找到返回 None
    """
    wanted = _normalize_text(name)
    for tool in tools:
        if tool.intent == wanted:
            return tool
    return None


def find_skill_tool(user_question: str, tools: list[SkillToolSpec], intent: str) -> SkillToolSpec | None:
    wanted = _normalize_text(intent)
    q = _normalize_text(user_question)
    candidates = [t for t in tools if t.intent == wanted]
    if not candidates:
        return None
    for tool in candidates:
        if any(trigger and trigger in q for trigger in tool.triggers):
            return tool
    return candidates[0]


def invoke_skill_tool(
    tool: SkillToolSpec,
    *,
    payload: dict[str, Any],
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    # 子进程 CWD 设为项目根目录，确保 .env 文件能被 pydantic Settings 正确加载
    # 路径: skills/tool-executor/scripts/order_query.py
    #   parents[0]=tool-executor/  parents[1]=skills/  parents[2]=root
    _script_dir = tool.script_path.resolve().parent
    _project_root_str = str(_script_dir.parents[2].resolve())

    proc = subprocess.run(
        [sys.executable, str(tool.script_path)],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        cwd=_project_root_str,
        check=False,
    )
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    # 优先解析 stdout JSON，有有效数据就按成功处理
    # （aiomysql 在 Windows 子进程退出时可能报 RuntimeError，导致 returncode≠0，但数据已正确输出）
    if stdout:
        try:
            data = json.loads(stdout)
            return {"ok": True, "result": data}
        except json.JSONDecodeError as exc:
            logger.debug("invoke_skill_tool: stdout is not valid JSON, will check returncode. "
                        "stdout[:200]=%s, error=%s", stdout[:200], exc)

    if proc.returncode != 0:
        return {
            "ok": False,
            "error": f"脚本退出码={proc.returncode}",
            "stderr": stderr,
            "stdout": stdout,
        }
    return {"ok": True, "result": {"message": "工具执行成功，但未返回内容"}}
