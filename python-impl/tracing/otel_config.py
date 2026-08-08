# python-impl\tracing\otel_config.py
# ============================================================
# 可观测性 — OpenTelemetry + Agent 指标 + Token 追踪
# ============================================================

from __future__ import annotations

import time
import logging
import threading
import contextvars
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ── Token 追踪 ──

@dataclass
class TokenUsage:
    """单次 LLM 调用的 token 消耗。"""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    model: str = ""


@dataclass
class AgentTokenStats:
    """单个 Agent 的 token 统计。"""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0
    models: set[str] = field(default_factory=set)

    def record(self, usage: TokenUsage) -> None:
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.total_tokens += usage.total_tokens
        self.calls += 1
        if usage.model:
            self.models.add(usage.model)


class TokenTracker:
    """async 安全的 token 追踪器，按 Agent 和全局维度统计。

    使用 contextvars 替代 threading.local：确保 async 协程间正确隔离 current_agent。
    TokenCallbackHandler 在每次 LLM 调用完成时写入，
    @trace_agent_call 在执行 Agent 前设置 current_agent 以归属。
    """

    def __init__(self) -> None:
        self._per_agent: dict[str, AgentTokenStats] = {}
        self._global_input: int = 0
        self._global_output: int = 0
        self._lock = threading.Lock()
        self._current_agent: contextvars.ContextVar[str] = contextvars.ContextVar(
            "current_agent", default=""
        )

    @property
    def current_agent(self) -> str:
        return self._current_agent.get()

    @current_agent.setter
    def current_agent(self, name: str) -> None:
        self._current_agent.set(name)

    def record(self, usage: TokenUsage) -> None:
        """记录一次 LLM token 消耗，归属到 current_agent。"""
        agent = self.current_agent or "unknown"
        with self._lock:
            if agent not in self._per_agent:
                self._per_agent[agent] = AgentTokenStats()
            self._per_agent[agent].record(usage)
            self._global_input += usage.input_tokens
            self._global_output += usage.output_tokens

    def get_per_agent(self) -> dict[str, dict[str, Any]]:
        """返回按 Agent 的 token 统计，供 /api/metrics 使用。"""
        with self._lock:
            result: dict[str, dict[str, Any]] = {}
            for agent, stats in self._per_agent.items():
                result[agent] = {
                    "input_tokens": stats.input_tokens,
                    "output_tokens": stats.output_tokens,
                    "total_tokens": stats.total_tokens,
                    "calls": stats.calls,
                    "models": sorted(stats.models),
                }
            return result

    def get_global(self) -> dict[str, int]:
        """返回全局 token 累计。"""
        with self._lock:
            return {
                "input_tokens": self._global_input,
                "output_tokens": self._global_output,
                "total_tokens": self._global_input + self._global_output,
            }


# 全局单例
token_tracker = TokenTracker()

# 流式输出开关：Agent 内部可设置此标记来阻止当前 LLM 调用的 token 流式输出
# 典型场景：ticket_handler._analyze() 产生内部 JSON，不应推送给用户
stream_blocked: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "stream_blocked", default=False,
)


# ── Agent 指标 ──

class AgentMetrics:
    """Agent 调用指标收集器（延迟、成功率）。"""

    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []

    def record(self, agent_name: str, duration_ms: float, success: bool,
               intent: str = "", error: str = "") -> None:
        self._records.append({
            "agent": agent_name,
            "duration_ms": duration_ms,
            "success": success,
            "intent": intent,
            "error": error,
            "timestamp": time.time(),
        })
        if len(self._records) > 200:
            self._records = self._records[-200:]

    def get_metrics(self, last_n: int = 50) -> list[dict[str, Any]]:
        return self._records[-last_n:]

    def get_stats(self) -> dict[str, dict[str, Any]]:
        """按 Agent 聚合：调用次数、成功率、平均延迟。"""
        if not self._records:
            return {}
        # 使用最近 200 条记录计算
        records = self._records[-200:]
        aggr: dict[str, dict[str, Any]] = {}
        for r in records:
            agent = r["agent"]
            if agent not in aggr:
                aggr[agent] = {"calls": 0, "success": 0, "total_duration_ms": 0.0}
            aggr[agent]["calls"] += 1
            aggr[agent]["total_duration_ms"] += r["duration_ms"]
            if r["success"]:
                aggr[agent]["success"] += 1

        result: dict[str, dict[str, Any]] = {}
        for agent, stats in aggr.items():
            calls = stats["calls"]
            result[agent] = {
                "calls": calls,
                "success_rate": stats["success"] / calls if calls else 0.0,
                "avg_duration_ms": round(stats["total_duration_ms"] / calls, 1) if calls else 0.0,
            }
        return result


# 全局单例
agent_metrics = AgentMetrics()


# ── OpenTelemetry 初始化 ──

def init_tracer(service_name: str = "smart-service-agent",
                endpoint: str = "http://localhost:4317",
                enabled: bool = False) -> None:
    """初始化 OpenTelemetry 追踪。"""
    if not enabled:
        logger.info("OpenTelemetry tracing disabled")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource

        resource = Resource(attributes={SERVICE_NAME: service_name})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        logger.info("OpenTelemetry tracing initialized (endpoint=%s)", endpoint)
    except ImportError:
        logger.warning("OpenTelemetry SDK not installed, tracing disabled")
    except Exception as exc:
        logger.warning("Failed to init OpenTelemetry: %s", exc)


# ── Agent 调用追踪装饰器 ──

def trace_agent_call(agent_name: str):
    """装饰器：追踪 Agent 方法调用，记录耗时、成败、token 归属。

    执行流程：
    1. 设置 token_tracker.current_agent = agent_name
    2. 执行 Agent 方法
    3. 记录指标
    4. 清除 current_agent
    """
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            token_tracker.current_agent = agent_name
            start = time.perf_counter()
            success = True
            error = ""
            intent = ""
            try:
                result = await fn(*args, **kwargs)
                if isinstance(result, dict):
                    intent = result.get("intent", "")
                return result
            except Exception as exc:
                success = False
                error = str(exc)
                raise
            finally:
                duration = (time.perf_counter() - start) * 1000
                agent_metrics.record(agent_name, duration, success, intent, error)
                token_tracker.current_agent = ""
        return wrapper
    return decorator


# ── Token Callback Handler ──

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult


class TokenCallbackHandler(BaseCallbackHandler):
    """LangChain 回调：捕获每次 LLM 调用的 token 消耗，写入 TokenTracker 和 MetricsCollector。"""

    def on_llm_start(self, *args: Any, **kwargs: Any) -> None:
        """LLM 调用开始时记录调用次数。"""
        from tracing.collector import collector
        collector.record_llm_call(is_error=False)

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """LLM 调用完成时提取 token_usage，兼容多种来源格式。"""
        from tracing.collector import collector

        token_usage: dict[str, int] = {}
        model_name = ""

        # 来源 1：llm_output（非流式 / OpenAI 兼容）
        if response.llm_output:
            token_usage = response.llm_output.get("token_usage", {})
            model_name = response.llm_output.get("model_name", "")
            logger.debug("TokenCallback on_llm_end: llm_output=%s", response.llm_output)

        # 来源 2：generations[0].message 的 usage_metadata / response_metadata
        if not token_usage and response.generations:
            gen = response.generations[0]
            if gen:
                msg = getattr(gen[0], "message", None) if gen else None
                if msg:
                    # LangChain >= 0.3: AIMessage.usage_metadata
                    um = getattr(msg, "usage_metadata", None) or {}
                    rm = getattr(msg, "response_metadata", None) or {}
                    logger.debug(
                        "TokenCallback on_llm_end: type=%s usage_metadata=%s response_metadata.token_usage=%s",
                        type(msg).__name__, dict(um), rm.get("token_usage", {}),
                    )
                    if um:
                        token_usage = {
                            "prompt_tokens": um.get("input_tokens", 0),
                            "completion_tokens": um.get("output_tokens", 0),
                            "total_tokens": um.get("total_tokens", 0),
                        }
                    # 来源 3：response_metadata.token_usage
                    if not token_usage:
                        token_usage = rm.get("token_usage", {})
                    if not model_name:
                        model_name = rm.get("model_name", "")

        if not token_usage:
            logger.warning(
                "TokenCallback on_llm_end: NO token_usage found! "
                "llm_output=%s has_generations=%s gen_type=%s",
                response.llm_output,
                bool(response.generations),
                type(response.generations[0][0]).__name__ if response.generations and response.generations[0] else "N/A",
            )
            return

        input_tokens = token_usage.get("prompt_tokens", 0)
        output_tokens = token_usage.get("completion_tokens", 0)
        total_tokens = token_usage.get("total_tokens", input_tokens + output_tokens)

        logger.info(
            "TokenCallback on_llm_end: input=%d output=%d total=%d model=%s agent=%s",
            input_tokens, output_tokens, total_tokens, model_name, token_tracker.current_agent,
        )

        usage = TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            model=model_name,
        )
        token_tracker.record(usage)
        collector.record_llm_tokens(input_tokens, output_tokens)

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        """LLM 调用出错时记录。"""
        logger.debug("LLM error in callback: %s", error)
        from tracing.collector import collector
        collector.record_llm_call(is_error=True)


def capture_llm_tokens(response: Any) -> None:
    """从 AIMessage 手动提取 token 用量并上报（兜底 callback handler 未触发）。

    尝试顺序：usage_metadata → response_metadata.token_usage
    """
    usage_meta = getattr(response, "usage_metadata", None) or {}
    resp_meta = getattr(response, "response_metadata", None) or {}

    input_tok = usage_meta.get("input_tokens", 0)
    output_tok = usage_meta.get("output_tokens", 0)

    if not input_tok and not output_tok:
        tu = resp_meta.get("token_usage", {})
        input_tok = tu.get("prompt_tokens", 0)
        output_tok = tu.get("completion_tokens", 0)

    if input_tok or output_tok:
        logger.info(
            "capture_llm_tokens: input=%d output=%d model=%s agent=%s",
            input_tok, output_tok, resp_meta.get("model_name", ""), token_tracker.current_agent,
        )
        collector.record_llm_tokens(input_tok, output_tok)
        usage = TokenUsage(
            input_tokens=input_tok,
            output_tokens=output_tok,
            total_tokens=input_tok + output_tok,
            model=resp_meta.get("model_name", ""),
        )
        token_tracker.record(usage)
    else:
        logger.warning(
            "capture_llm_tokens: NO tokens found! usage_metadata=%s response_metadata=%s",
            dict(usage_meta), {k: v for k, v in dict(resp_meta).items() if k != "headers"},
        )
