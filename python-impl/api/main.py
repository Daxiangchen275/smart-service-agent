# ============================================================
# FastAPI 服务入口 — 智能客服多 Agent 系统
# ============================================================

from __future__ import annotations

import os
import uuid
import time
import logging
from logging.handlers import TimedRotatingFileHandler
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage

from infra import settings, resolve_llm_base_url, init_mysql_pool, close_mysql_pool
from tracing.middleware import MonitoringMiddleware
from tracing.collector import collector
from scripts.skill_runtime import load_skill_tools, find_tool_by_name, invoke_skill_tool, SkillToolSpec
from memory import WorkingMemory, ShortTermMemory, ChromaKnowledgeStore
from agents.supervisor import AgentState, create_supervisor_graph
from tracing.otel_config import init_tracer, agent_metrics, token_tracker, TokenCallbackHandler
from api.startup_kb import initialize_knowledge_base_background

# ── 日志 ──

_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

# 控制台输出 (stdout, 容器环境通过 docker logs 采集)
_console = logging.StreamHandler()
_console.setFormatter(_formatter)

# 根日志器 - 避免重复添加 handler（uvicorn reload 时会重新导入本模块）
_root = logging.getLogger()
_root.setLevel(logging.DEBUG if settings.debug else logging.INFO)

# 检查是否已添加过同类型 handler, 防止重复输出
_has_console = any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
                   for h in _root.handlers)
if not _has_console:
    _root.addHandler(_console)

# 文件日志: 仅在开启调试模式或显式配置 LOG_DIR 时启用
if settings.debug or settings.log_dir:
    _log_dir = settings.log_dir or os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    os.makedirs(_log_dir, exist_ok=True)

    def _daily_namer(default_name: str) -> str:
        """将 TimedRotatingFileHandler 默认名 logs/app.log.2026-08-02 转为 logs/2026-08-02.log"""
        import re
        m = re.search(r'(\d{4}-\d{2}-\d{2})', default_name)
        if m:
            return os.path.join(os.path.dirname(default_name), f"{m.group(1)}.log")
        return default_name

    _has_file = any(isinstance(h, TimedRotatingFileHandler) for h in _root.handlers)
    if not _has_file:
        _file = TimedRotatingFileHandler(
            os.path.join(_log_dir, "app.log"),
            when="midnight", interval=1, backupCount=10, encoding="utf-8",
        )
        _file.namer = _daily_namer
        _file.suffix = "%Y-%m-%d"
        _file.setFormatter(_formatter)
        _root.addHandler(_file)

logger = logging.getLogger("api")

# ── 全局单例 ──

working_memory: WorkingMemory | None = None
short_term_memory: ShortTermMemory | None = None
knowledge_store: ChromaKnowledgeStore | None = None
skill_tools: list[SkillToolSpec] = []
graph = None
start_time: float = 0.0
_service_ready: bool = False  # 就绪探针标志, 所有初始化完成后置为 True


# ── 生命周期 ──

@asynccontextmanager
async def lifespan(app: FastAPI):
    global working_memory, short_term_memory, knowledge_store
    global skill_tools, graph, start_time, _service_ready

    logger.info("=" * 60)
    logger.info("智能客服多 Agent 系统启动中...")
    logger.info("=" * 60)

    # 1. OpenTelemetry
    init_tracer(
        service_name="smart-service-agent",
        endpoint=settings.otel_endpoint,
        enabled=settings.otel_enabled,
    )

    # 2. LLM（双模型：streaming 给 SSE 流式输出，internal 给内部调用确保 token 统计准确）
    streaming_llm, internal_llm = build_chat_models(settings)
    logger.info("LLM initialized: %s @ %s (dual-model: streaming + internal)",
                settings.llm_model, resolve_llm_base_url())

    # 2b. MySQL（不可用时优雅降级，不阻塞启动）
    await init_mysql_pool(settings)

    # 3. 记忆系统
    working_memory = WorkingMemory(max_records=settings.wm_max_records)
    short_term_memory = ShortTermMemory(
        max_rounds=settings.stm_max_rounds,
        md_dir=settings.stm_md_dir,
    )
    knowledge_store = ChromaKnowledgeStore(settings)

    # 4. 从 SKILL.MD 加载工具列表（所有 Agent 统一使用 skill_runtime）
    skills_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skills")
    skill_tools = load_skill_tools(skills_dir)
    logger.info("skill_runtime tools loaded from SKILL.MD: %d tools", len(skill_tools))
    for t in skill_tools:
        logger.info("  - intent=%s script=%s", t.intent, t.script_path.name)

    # 5. LangGraph 编排图
    graph = create_supervisor_graph(
        streaming_llm=streaming_llm,
        internal_llm=internal_llm,
        working_memory=working_memory,
        short_term_memory=short_term_memory,
        knowledge_store=knowledge_store,
        skill_tools=skill_tools,
        compliance_enabled=settings.compliance_enabled,
        sensitive_words=settings.compliance_sensitive_words,
    )
    logger.info("LangGraph supervisor graph compiled")

    # 6. 后台知识库初始化
    initialize_knowledge_base_background(settings, knowledge_store)

    start_time = time.time()
    _service_ready = True
    logger.info("服务启动完成 (port=%d)", settings.port)
    logger.info("   前端页面:   http://127.0.0.1:%d", settings.port)
    logger.info("   Swagger UI: http://localhost:%d/docs", settings.port)
    logger.info("   健康检查:   http://localhost:%d/health", settings.port)

    yield

    # 关闭阶段
    await close_mysql_pool()
    logger.info("服务关闭")


# ── FastAPI 应用 ──

app = FastAPI(
    title="智能客服多Agent系统",
    description="基于 LangGraph 的 Supervisor 编排多 Agent 智能客服系统",
    version="1.2.0",
    lifespan=lifespan,
)

# CORS 中间件 - 来源由环境变量 CORS_ORIGINS 控制，逗号分隔
_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
if not _cors_origins:
    _cors_origins = ["http://localhost:3000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)


# ── API Key 鉴权中间件 ──

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_SKIP_AUTH_PATHS = {"/health", "/", "/docs", "/openapi.json", "/static"}


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """API Key 鉴权中间件: 从 Authorization: Bearer <key> 或 X-API-Key 头中提取密钥。

    启用条件: API_AUTH_ENABLED=true 且 API_AUTH_KEY 已配置。
    白名单路径: /health, /, /docs, /openapi.json, /static
    """

    async def dispatch(self, request: Request, call_next):
        # 白名单路径跳过鉴权
        if request.url.path in _SKIP_AUTH_PATHS or request.url.path.startswith("/static"):
            return await call_next(request)

        # 未启用鉴权时直接放行
        if not settings.api_auth_enabled or not settings.api_auth_key:
            return await call_next(request)

        # 提取密钥: Authorization: Bearer <key> 或 X-API-Key: <key>
        auth_header = request.headers.get("Authorization", "")
        api_key = ""
        if auth_header.startswith("Bearer "):
            api_key = auth_header[7:]
        else:
            api_key = request.headers.get("X-API-Key", "")

        if api_key != settings.api_auth_key:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"},
            )

        return await call_next(request)


app.add_middleware(ApiKeyMiddleware)
app.add_middleware(MonitoringMiddleware)

# 静态文件
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir, html=True), name="static")


# ── 前端重定向 ──

@app.get("/")
async def root():
    """重定向到前端页面。"""
    return RedirectResponse(url="/static/index.html")


# ── Pydantic 模型 ──

class ChatRequest(BaseModel):
    message: str
    user_id: str = "user-1001"
    session_id: str | None = None


class ChatResponse(BaseModel):
    response: str
    session_id: str
    intent: str
    intent_result: dict
    sub_results: dict
    tool_calls: list
    tool_results: dict
    compliance_passed: bool


class ToolCallRequest(BaseModel):
    name: str
    arguments: dict[str, Any] = {}


# ── 端点 ──

@app.get("/health")
async def health():
    """存活探针 (liveness): 进程是否存活。始终返回 healthy 只要 FastAPI 能响应。"""
    return {
        "status": "alive",
        "uptime_seconds": round(time.time() - start_time, 1) if start_time > 0 else 0,
    }


@app.get("/ready")
async def ready():
    """就绪探针 (readiness): 服务是否可以接收请求。

    Kubernetes 就绪探针应指向此端点。
    返回 200 表示所有组件初始化完成; 返回 503 表示仍在初始化中。
    """
    if not _service_ready or graph is None:
        raise HTTPException(status_code=503, detail="Service initializing")

    kb_count = knowledge_store.document_count if knowledge_store else 0
    return {
        "status": "ready",
        "uptime_seconds": round(time.time() - start_time, 1) if start_time > 0 else 0,
        "knowledge_base": {
            "documents": kb_count,
        },
        "tools_from_skill_md": len(skill_tools) if skill_tools else 0,
    }


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """主聊天接口。"""
    if graph is None or short_term_memory is None:
        raise HTTPException(status_code=503, detail="系统初始化中，请稍后重试")

    # 局部绑定，帮助类型检查器窄化
    stm = short_term_memory

    # 1. 生成/复用 session_id
    session_id = request.session_id or f"sess-{uuid.uuid4().hex[:12]}"

    # 2. 写入用户消息到短期记忆
    try:
        await stm.add_message(session_id, "user", request.message, user_id=request.user_id)
    except Exception as exc:
        logger.warning("Failed to save user message: %s", exc)

    # 3. 读取最近 20 轮历史 → 构建 LangChain messages
    try:
        history = await stm.get_history(session_id, last_n=20, user_id=request.user_id)
        messages = []
        for msg in history:
            if msg["role"] == "user":
                messages.append(HumanMessage(content=msg["content"]))
            else:
                messages.append(AIMessage(content=msg["content"]))
    except Exception as exc:
        logger.warning("Failed to load history: %s", exc)
        messages = []

    # 添加当前消息
    messages.append(HumanMessage(content=request.message))

    # 4. 构造初始状态
    initial_state: AgentState = {
        "messages": messages,
        "user_id": request.user_id,
        "session_id": session_id,
        "intent": "",
        "intent_result": {},
        "tool_calls": [],
        "tool_results": {},
        "sub_results": {},
        "compliance_passed": True,
        "final_response": "",
        "current_agent": "",
        "retry_count": 0,
    }

    # 5. 执行 LangGraph
    try:
        _pipeline_start = time.perf_counter()
        result = await graph.ainvoke(
            initial_state,
            config={"configurable": {"thread_id": session_id}},
        )
        collector.record_pipeline((time.perf_counter() - _pipeline_start) * 1000)
    except Exception as exc:
        logger.error("Graph execution failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"处理失败: {str(exc)}") from exc

    # 6. 写入 assistant 回复到短期记忆
    final_response = result.get("final_response", "")
    try:
        await stm.add_message(session_id, "assistant", final_response,
                              user_id=request.user_id)
    except Exception as exc:
        logger.warning("Failed to save assistant message: %s", exc)

    # 7. 构建响应
    return ChatResponse(
        response=final_response,
        session_id=session_id,
        intent=result.get("intent", ""),
        intent_result=result.get("intent_result", {}),
        sub_results=result.get("sub_results", {}),
        tool_calls=result.get("tool_calls", []),
        tool_results=result.get("tool_results", {}),
        compliance_passed=result.get("compliance_passed", True),
    )


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest):
    """流式聊天接口 (SSE)。直接流式输出 LLM 生成的 token。

    相较于 /api/chat 的阻塞式完整返回，本端点使用 Server-Sent Events
    逐 token 推送，前端可实现打字机效果。

    格式：data: {"token": "..."} 或 data: {"type": "meta", ...} 或 data: [DONE]
    """
    import json as _json

    if graph is None or short_term_memory is None:
        raise HTTPException(status_code=503, detail="系统初始化中")

    stm = short_term_memory
    session_id = request.session_id or f"sess-{uuid.uuid4().hex[:12]}"

    # 保存用户消息
    try:
        await stm.add_message(session_id, "user", request.message, user_id=request.user_id)
    except Exception:
        pass

    # 读取历史
    try:
        history = await stm.get_history(session_id, last_n=20, user_id=request.user_id)
        messages = []
        for msg in history:
            if msg["role"] == "user":
                messages.append(HumanMessage(content=msg["content"]))
            else:
                messages.append(AIMessage(content=msg["content"]))
    except Exception:
        messages = []
    messages.append(HumanMessage(content=request.message))

    initial_state: AgentState = {
        "messages": messages,
        "user_id": request.user_id,
        "session_id": session_id,
        "intent": "",
        "intent_result": {},
        "tool_calls": [],
        "tool_results": {},
        "sub_results": {},
        "compliance_passed": True,
        "final_response": "",
        "current_agent": "",
        "retry_count": 0,
    }

    async def event_generator():
        full_response = ""
        _suppress_json = False  # 是否正在过滤内部 JSON
        _json_depth = 0         # brace 嵌套深度
        meta_sent: set[str] = set()
        # 流式白名单：只有这些图节点内的 LLM token 才推送
        _STREAM_NODES = {"knowledge_rag", "ticket_handler", "tool_executor", "synthesize", "human_handoff"}
        _BLOCK_NODES = {"intent_router", "compliance_check"}
        _current_node = ""
        _ttft_recorded = False
        _req_start = time.perf_counter()

        def _emit(kind: str, data: dict) -> str:
            return f"data: {_json.dumps({'type': kind, **data}, ensure_ascii=False)}\n\n"

        def _has_sub_key(output: dict, key: str) -> bool:
            """检查 output.sub_results 中是否有指定 key。"""
            sr = output.get("sub_results", {})
            return isinstance(sr, dict) and key in sr

        try:
            assert graph is not None, "Graph not initialized"
            _pipeline_start = time.perf_counter()
            async for event in graph.astream_events(
                initial_state,
                config={"configurable": {"thread_id": session_id}},
                version="v2",
            ):
                ev_kind = event.get("event", "")
                ev_name = event.get("name", "")

                # ── 节点进入/退出追踪 ──
                if ev_kind == "on_chain_start":
                    if any(n in ev_name for n in _STREAM_NODES):
                        _current_node = ev_name
                    elif any(n in ev_name for n in _BLOCK_NODES):
                        _current_node = ""

                if ev_kind == "on_chain_end":
                    if ev_name == _current_node:
                        _current_node = ""
                    output = event.get("data", {}).get("output", {})
                    if not isinstance(output, dict):
                        continue

                    # IntentRouter: 输出中有 intent 字段
                    if "intent" not in meta_sent and "intent" in output:
                        meta_sent.add("intent")
                        ir = output.get("intent_result", {})
                        sr = output.get("sub_results", {}).get("intent_router", {})
                        yield _emit("intent", {
                            "primary": ir.get("primary_intent", "?"),
                            "secondary": ir.get("secondary_intent", ""),
                            "agent": output.get("intent", ""),
                            "confidence": ir.get("confidence", 0),
                            "reasoning": ir.get("reasoning", ""),
                            "entities": ir.get("entities", {}),
                            "original_text": sr.get("raw_input", ""),
                            "rewritten_text": sr.get("rewritten_input", ""),
                        })
                    # KnowledgeRAG: sub_results 中有 knowledge_rag
                    if "rag" not in meta_sent and _has_sub_key(output, "knowledge_rag"):
                        meta_sent.add("rag")
                        rag = output.get("sub_results", {}).get("knowledge_rag", {})
                        yield _emit("rag", {
                            "query": rag.get("query", ""),
                            "documents_count": rag.get("documents_count", 0),
                            "documents": rag.get("documents", []),
                        })

                    # ComplianceChecker: 输出中有 compliance_passed
                    if "compliance" not in meta_sent and "compliance_passed" in output:
                        meta_sent.add("compliance")
                        yield _emit("compliance", {
                            "passed": output.get("compliance_passed", True),
                        })

                    # TicketHandler: sub_results 中有 ticket_handler
                    for agent_key in ("ticket_handler", "tool_executor"):
                        tk = f"tools:{agent_key}"
                        if tk not in meta_sent and _has_sub_key(output, agent_key):
                            meta_sent.add(tk)
                            info = output.get("sub_results", {}).get(agent_key, {})
                            results = info.get("results", [])
                            yield _emit("tools", {
                                "agent": agent_key,
                                "calls": [{"tool": r.get("tool", ""),
                                          "success": r.get("success", False)}
                                         for r in (results or [])],
                            })

                # ── 流式输出 LLM token（仅白名单节点内）──
                if ev_kind == "on_chat_model_stream" and _current_node:
                    chunk = event.get("data", {}).get("chunk", None)
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        token = chunk.content
                        # 过滤内部 JSON 分析输出（如 ticket_handler._analyze 的原始 JSON）。
                        # 机制：首个非空白字符为 { 或 [ → 进入过滤模式，
                        # 用 brace 深度计数跳过所有 token，直到嵌套归零后自动退出。
                        if not _suppress_json:
                            stripped = token.lstrip()
                            if stripped and stripped[0] in "{[":
                                _suppress_json = True
                                _json_depth = 0
                        if _suppress_json:
                            for ch in token:
                                if ch in "{[":
                                    _json_depth += 1
                                elif ch in "}]":
                                    _json_depth -= 1
                            if _json_depth <= 0:
                                _suppress_json = False
                            continue
                        if not _ttft_recorded:
                            _ttft_recorded = True
                            collector.record_ttft((time.perf_counter() - _req_start) * 1000)
                        full_response += token
                        yield f"data: {_json.dumps({'token': token}, ensure_ascii=False)}\n\n"

            collector.record_pipeline((time.perf_counter() - _pipeline_start) * 1000)

        except Exception as exc:
            logger.error("Stream failed: %s", exc)
            yield f"data: {_json.dumps({'type': 'error', 'message': str(exc)}, ensure_ascii=False)}\n\n"

        finally:
            yield "data: [DONE]\n\n"
            # 保存 assistant 回复
            if full_response:
                try:
                    await stm.add_message(session_id, "assistant", full_response,
                                          user_id=request.user_id)
                except Exception:
                    pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",       # 禁用 nginx 缓冲
        },
    )
@app.get("/api/history/{session_id}")
async def get_history(session_id: str):
    """获取会话历史。"""
    stm = short_term_memory
    if stm is None:
        raise HTTPException(status_code=503, detail="系统初始化中")

    history = await stm.get_history(session_id, last_n=0)  # 0 = all
    return {"session_id": session_id, "messages": history, "count": len(history)}


@app.get("/api/tools")
async def list_tools():
    """列出所有 SKILL.MD 中定义的工具（来自 skill_runtime 解析）。"""
    if not skill_tools:
        return {"tools": [], "count": 0}

    result = []
    for t in skill_tools:
        result.append({
            "name": t.intent,
            "description": t.description,
            "skill": t.name,
            "script": str(t.script_path.name),
            "triggers": list(t.triggers),
        })
    return {"tools": result, "count": len(result)}


@app.post("/api/tools/call")
async def call_tool(request: ToolCallRequest):
    """直接调用 SKILL.MD 工具（通过 skill_runtime 子进程执行，调试/集成用）。"""
    if not skill_tools:
        raise HTTPException(status_code=503, detail="没有可用的工具")

    tool_spec = find_tool_by_name(request.name, skill_tools)
    if tool_spec is None:
        raise HTTPException(status_code=404, detail=f"未找到工具: {request.name}")

    raw = invoke_skill_tool(tool_spec, payload=request.arguments)
    return {
        "tool_name": request.name,
        "success": raw.get("ok", False),
        "result": raw.get("result"),
        "error": raw.get("error"),
    }


@app.get("/api/metrics")
async def get_metrics():
    """全维度监控指标。"""
    snapshot = collector.snapshot()
    snapshot["agent_stats"] = agent_metrics.get_stats()
    snapshot["agent_tokens"] = token_tracker.get_per_agent()
    snapshot["tokens_total"] = token_tracker.get_global()
    snapshot["tools_available"] = len(skill_tools) if skill_tools else 0
    return snapshot


# ── 评测系统 API ──

# 缓存最近一次评测结果
_eval_cache: dict[str, Any] | None = None
_eval_running: bool = False


@app.get("/api/evals")
async def get_evals():
    """获取评测结果（仅返回缓存或状态，不自动触发）。"""
    global _eval_cache, _eval_running
    if _eval_cache is not None:
        return _eval_cache
    if _eval_running:
        return {"status": "running", "message": "评测正在执行中，请稍后刷新"}
    return {"status": "idle", "message": "尚未执行评测，点击「运行评测」开始"}


@app.post("/api/evals/run")
async def run_evals():
    """强制重新执行评测（后台异步）。"""
    global _eval_cache, _eval_running
    if _eval_running:
        return {"status": "running", "message": "评测正在执行中，请稍后重试"}
    import asyncio
    _eval_running = True
    _eval_cache = None  # 清除旧缓存
    asyncio.create_task(_run_evals_background())
    return {"status": "running", "message": "评测已启动，请稍后刷新"}


async def _run_evals_background():
    """后台任务：执行评测并缓存结果。"""
    global _eval_cache, _eval_running
    try:
        _eval_cache = await _run_all_evals()
    except Exception as exc:
        logger.error("Eval background task failed: %s", exc)
        _eval_cache = {"status": "error", "message": str(exc)}
    finally:
        _eval_running = False


async def _run_all_evals() -> dict[str, Any]:
    """在后台线程执行全部评测套件，避免阻塞事件循环。"""
    import asyncio
    import concurrent.futures
    loop = asyncio.get_running_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return await loop.run_in_executor(pool, _run_all_evals_sync)


def _run_all_evals_sync() -> dict[str, Any]:
    """同步执行全部评测套件（在线程中运行）。"""
    import time as _time
    from evals.runner import EvalRunner
    from evals.test_cases import INTENT_CASES, ENTITY_CASES, RAG_CASES, E2E_CASES

    start = _time.time()
    runner = EvalRunner(base_url=f"http://127.0.0.1:{settings.port}", timeout=120)

    suites = {}
    errors: list[str] = []
    total_cases = 0
    total_passed = 0

    # 依次执行四个套件
    for suite_name, method in [
        ("intent", runner.eval_intent),
        ("entity", runner.eval_entity),
        ("rag", runner.eval_rag),
        ("e2e", runner.eval_e2e),
    ]:
        try:
            report = method()
            suites[suite_name] = {
                "total": report.total,
                "passed": report.passed,
                "pass_rate": round(report.passed / report.total, 3) if report.total else 1.0,
                "accuracy": round(report.accuracy, 3),
                "entity_f1": round(report.entity_f1, 3),
                "keyword_coverage": round(report.keyword_coverage, 3),
                "ece": round(report.ece, 4),
                "duration_seconds": round(report.duration_seconds, 1),
                "failure_count": len([d for d in report.details if not d.get("passed")]),
                "error_count": len(report.errors),
                "details": [
                    {
                        "case": d.get("case", "")[:80],
                        "passed": d.get("passed", False),
                        "expected_intent": d.get("expected_intent", ""),
                        "predicted_intent": d.get("predicted_intent", ""),
                        "keyword_coverage": d.get("keyword_coverage"),
                    }
                    for d in report.details
                ],
            }
            total_cases += report.total
            total_passed += report.passed
            if report.errors:
                errors.extend(report.errors)
        except Exception as exc:
            logger.error("Eval suite '%s' failed: %s", suite_name, exc)
            suites[suite_name] = {"error": str(exc), "total": 0, "passed": 0}
            errors.append(f"[{suite_name}] {exc}")

    runner.close()
    elapsed = round(_time.time() - start, 1)

    return {
        "status": "completed",
        "total_cases": total_cases,
        "total_passed": total_passed,
        "overall_pass_rate": round(total_passed / total_cases, 3) if total_cases else 0.0,
        "duration_seconds": elapsed,
        "errors": errors[:20],
        "suites": suites,
        "test_case_counts": {
            "intent": len(INTENT_CASES),
            "entity": len(ENTITY_CASES),
            "rag": len(RAG_CASES),
            "e2e": len(E2E_CASES),
        },
    }


# ── 辅助函数 ──

def build_chat_models(settings):
    """构建两个 ChatOpenAI 实例，注入 TokenCallbackHandler。

    Returns:
        (streaming_llm, internal_llm)
        - streaming_llm: streaming=True，用于 SSE 流式输出（用户可见的回复生成）
        - internal_llm:  streaming=False，用于内部 LLM 调用，确保 token 统计准确
    """
    _common: dict[str, Any] = dict(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=resolve_llm_base_url(),
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,  # type: ignore[call-arg]
    )
    streaming_llm = ChatOpenAI(  # type: ignore
        **_common,
        streaming=True,        # SSE astream_events 的 on_chat_model_stream 依赖此选项
        stream_usage=True,     # 流式模式下也尝试获取 token_usage
        callbacks=[TokenCallbackHandler()],
    )
    internal_llm = ChatOpenAI(  # type: ignore
        **_common,
        streaming=False,       # 非流式：DeepSeek API 天然返回 usage，token 监控准确
        callbacks=[TokenCallbackHandler()],
    )
    return streaming_llm, internal_llm


# ── 入口 ──

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
