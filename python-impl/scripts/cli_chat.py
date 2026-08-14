#!/usr/bin/env python3
# ============================================================
# 终端对话脚本 -- 支持流式和非流式两种模式
# ============================================================
"""
用法：
    python scripts/cli_chat.py              # 非流式（默认）
    python scripts/cli_chat.py --stream     # 流式输出（打字机效果）
    python scripts/cli_chat.py --user-id user-1002
"""

from __future__ import annotations

import argparse
import uuid
import sys
import json

import httpx


def main():
    parser = argparse.ArgumentParser(description="智能客服终端对话")
    parser.add_argument("--user-id", default="user-1001", help="用户 ID")
    parser.add_argument("--port", type=int, default=8000, help="服务端口")
    parser.add_argument("--stream", action="store_true", help="启用流式输出")
    args = parser.parse_args()

    base_url = f"http://127.0.0.1:{args.port}/api/chat"
    stream_url = f"http://127.0.0.1:{args.port}/api/chat/stream"
    session_id = f"cli-{uuid.uuid4().hex[:8]}"
    use_stream = args.stream

    print("=" * 56)
    print("  智能客服终端对话" + (" (流式)" if use_stream else ""))
    print(f"  用户: {args.user_id}  会话: {session_id}")
    print("  输入 quit/exit/q 退出, --stream 切换流式")
    print("=" * 56)

    client = httpx.Client(timeout=120)

    while True:
        try:
            user_input = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("再见！")
            break

        if use_stream:
            chat_stream(client, stream_url, user_input, args.user_id, session_id)
        else:
            chat_block(client, base_url, user_input, args.user_id, session_id)


def chat_stream(client: httpx.Client, url: str, message: str,
                user_id: str, session_id: str) -> None:
    """流式 SSE 模式。AI 回复先出，元数据后出。"""
    agent_labels = {
        "knowledge_rag": "KnowledgeRAG",
        "ticket_handler": "TicketHandler",
        "tool_executor": "ToolExecutor",
        "human_handoff": "HumanHandoff",
    }

    try:
        with client.stream(
            "POST", url,
            json={"message": message, "user_id": user_id, "session_id": session_id},
            timeout=120,
        ) as resp:
            if resp.status_code != 200:
                print(f"\n[请求失败] HTTP {resp.status_code}")
                return

            print()
            meta_lines: list[str] = []
            response_started = False

            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break

                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                tp = data.get("type", "")

                # ── 意图 ──
                if tp == "intent":
                    primary = data.get("primary", "?")
                    secondary = data.get("secondary", "")
                    agent = data.get("agent", "")
                    conf = data.get("confidence", 0)
                    entities = data.get("entities", {})
                    original = data.get("original_text", "")
                    rewritten = data.get("rewritten_text", "")

                    reasoning = data.get("reasoning", "")
                    line1 = f"[IntentRouter] 意图={primary}"
                    if secondary:
                        line1 += f"({secondary})"
                    line1 += f"  路由→{agent_labels.get(agent, agent)}  置信度={conf:.0%}"
                    meta_lines.append(line1)
                    if reasoning:
                        meta_lines.append(f"               思考: {reasoning}")
                    if original and rewritten and original != rewritten:
                        meta_lines.append(f"[IntentRouter] 查询改写: \"{original}\" -> \"{rewritten}\"")
                    if entities:
                        meta_lines.append(f"               实体={entities}")

                # ── RAG 检索 ──
                elif tp == "rag":
                    query = data.get("query", "")
                    count = data.get("documents_count", 0)
                    docs = data.get("documents", [])
                    if query:
                        meta_lines.append(f"[KnowledgeRAG] 改写查询: {query}")
                    if count:
                        meta_lines.append(f"[KnowledgeRAG] 检索到 {count} 篇文档:")
                        for d in docs:
                            meta_lines.append(f"            - {d}")

                # ── 合规 ──
                elif tp == "compliance":
                    passed = data.get("passed", True)
                    status = "通过" if passed else "转人工"
                    meta_lines.append(f"[Compliance] 合规审查: {status}")

                # ── 工具调用 ──
                elif tp == "tools":
                    agent = data.get("agent", "")
                    calls = data.get("calls", [])
                    for c in calls:
                        ok = "OK" if c.get("success") else "FAIL"
                        meta_lines.append(f"[{agent_labels.get(agent, agent)}] 调用 {c.get('tool', '?')}: {ok}")

                # ── 错误 ──
                elif tp == "error":
                    meta_lines.append(f"[错误] {data.get('message', '')}")

                # ── 流式 token ──
                elif "token" in data:
                    if not response_started:
                        response_started = True
                        print("─" * 40)
                        sys.stdout.write("AI: ")
                    sys.stdout.write(data["token"])
                    sys.stdout.flush()

            # 流式结束 → 换行 + 分隔线
            if response_started:
                print()
            print("─" * 40)

            # 最后打印所有元数据
            for ml in meta_lines:
                print(ml)
            print("─" * 40)

    except Exception as exc:
        print(f"\n[请求失败] {exc}")


def chat_block(client: httpx.Client, url: str, message: str,
               user_id: str, session_id: str) -> None:
    """非流式阻塞模式（完整返回）。"""
    try:
        resp = client.post(url, json={
            "message": message,
            "user_id": user_id,
            "session_id": session_id,
        })
        data = resp.json()
    except Exception as exc:
        print(f"\n[请求失败] {exc}")
        return

    # ── 打印各模块日志 ──

    intent_result = data.get("intent_result", {})
    sub_results = data.get("sub_results", data.get("intent_result", {}))
    compliance = data.get("compliance_passed", True)
    reply = data.get("response", "(无回复)")

    print()
    print("─" * 40)

    # 1. IntentRouter
    primary = intent_result.get("primary_intent", "?")
    secondary = intent_result.get("secondary_intent", "")
    confidence = intent_result.get("confidence", 0)
    suggested = intent_result.get("suggested_agent", "?")
    entities = intent_result.get("entities", {})

    agent_label = {
        "knowledge_rag": "KnowledgeRAG",
        "ticket_handler": "TicketHandler",
        "tool_executor": "ToolExecutor",
        "human_handoff": "HumanHandoff",
    }.get(suggested, suggested)

    # 查询改写（仅在改写前后不一致时打印）
    router_data = sub_results.get("intent_router", {}) if isinstance(sub_results, dict) else {}
    raw_input = router_data.get("raw_input", "")
    rewritten_input = router_data.get("rewritten_input", "")
    if raw_input and rewritten_input and raw_input != rewritten_input:
        print(f"[IntentRouter] 查询改写: \"{raw_input}\" -> \"{rewritten_input}\"")

    print(f"[IntentRouter] 意图={primary}", end="")
    if secondary:
        print(f"({secondary})", end="")
    print(f"  路由→{agent_label}  置信度={confidence:.0%}")
    if entities:
        print(f"               实体={entities}")

    # 2. 子 Agent
    for agent_key, agent_data in sub_results.items():
        if isinstance(agent_data, dict):
            agent_name = agent_data.get("agent", agent_key)
            if "query" in agent_data:
                print(f"[{agent_name}] 改写查询: {agent_data['query']}")
            doc_count = agent_data.get("documents_count", 0)
            doc_list = agent_data.get("documents", [])
            if doc_count:
                print(f"[{agent_name}] 检索到 {doc_count} 篇文档:")
                for d in doc_list:
                    print(f"            - {d}")
            if "results" in agent_data:
                results = agent_data["results"]
                for r in results:
                    tool = r.get("tool", "?")
                    ok = "OK" if r.get("success") else "FAIL"
                    print(f"[{agent_name}] 调用 {tool}: {ok}")

    # 3. 合规
    compliance_status = "通过" if compliance else "转人工"
    print(f"[Compliance] 合规审查: {compliance_status}")

    # 4. 最终回复
    print("─" * 40)
    print(f"AI: {reply}")


if __name__ == "__main__":
    main()
