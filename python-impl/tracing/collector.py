# ============================================================
# MetricsCollector — 全维度指标采集单例
# ============================================================

from __future__ import annotations

import os
import time
import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _Bucket:
    """滑动窗口计数器。"""
    values: list[float] = field(default_factory=list)
    _max_size: int = 500

    def add(self, value: float) -> None:
        self.values.append(value)
        if len(self.values) > self._max_size:
            self.values = self.values[-self._max_size:]

    def stats(self) -> dict[str, float]:
        if not self.values:
            return {"count": 0, "avg": 0, "p50": 0, "p95": 0, "p99": 0}
        sorted_vals = sorted(self.values)
        n = len(sorted_vals)
        return {
            "count": n,
            "avg": round(sum(sorted_vals) / n, 1),
            "p50": round(sorted_vals[int(n * 0.50)], 1),
            "p95": round(sorted_vals[int(n * 0.95)], 1),
            "p99": round(sorted_vals[min(int(n * 0.99), n - 1)], 1),
        }


# ── 单例 ──

class MetricsCollector:
    """全维度指标采集器（线程安全）。

    使用方式：
        from tracing.collector import collector
        collector.record_intent("consultation", "knowledge_rag", 0.92, True)
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._start_time = time.time()

        # ── 请求级 ──
        self._request_total: int = 0
        self._request_errors: int = 0
        self._request_duration = _Bucket()
        self._pipeline_duration = _Bucket()
        self._ttft = _Bucket()             # Time To First Token

        # ── 意图路由 ──
        self._intent_dist: dict[str, int] = {}
        self._intent_confidences = _Bucket()
        self._rewrite_total: int = 0
        self._rewrite_triggered: int = 0
        self._human_handoff_total: int = 0

        # ── RAG 检索 ──
        self._rag_search_latency = _Bucket()
        self._rag_rerank_latency = _Bucket()
        self._rag_rerank_fallback: int = 0
        self._rag_rerank_total: int = 0
        self._rag_empty_results: int = 0
        self._rag_searches_total: int = 0

        # ── 工具执行 ──
        self._tool_calls: dict[str, int] = {}
        self._tool_success: dict[str, int] = {}
        self._tool_duration: dict[str, _Bucket] = {}
        self._tool_fallback: int = 0

        # ── LLM 调用 ──
        self._llm_errors: int = 0
        self._llm_calls: int = 0
        self._llm_input_tokens: int = 0
        self._llm_output_tokens: int = 0

        # ── 合规 ──
        self._compliance_total: int = 0
        self._compliance_failed: int = 0

    # ── 请求级 ──

    def record_request(self, duration_ms: float, is_error: bool = False) -> None:
        with self._lock:
            self._request_total += 1
            self._request_duration.add(duration_ms)
            if is_error:
                self._request_errors += 1

    def record_pipeline(self, duration_ms: float) -> None:
        with self._lock:
            self._pipeline_duration.add(duration_ms)

    def record_ttft(self, duration_ms: float) -> None:
        """记录首 token 延迟 (TTFT)。"""
        with self._lock:
            self._ttft.add(duration_ms)

    # ── 意图路由 ──

    def record_intent(self, primary: str, agent: str, confidence: float,
                      rewrite_occurred: bool = False,
                      is_handoff: bool = False) -> None:
        with self._lock:
            self._intent_dist[primary] = self._intent_dist.get(primary, 0) + 1
            self._intent_dist[f"route:{agent}"] = self._intent_dist.get(f"route:{agent}", 0) + 1
            self._intent_confidences.add(confidence)
            if rewrite_occurred:
                self._rewrite_triggered += 1
                self._rewrite_total += 1
            else:
                self._rewrite_total += 1
            if is_handoff:
                self._human_handoff_total += 1

    # ── RAG 检索 ──

    def record_rag_search(self, latency_ms: float, result_count: int = 0) -> None:
        with self._lock:
            self._rag_search_latency.add(latency_ms)
            self._rag_searches_total += 1
            if result_count == 0:
                self._rag_empty_results += 1

    def record_rag_rerank(self, latency_ms: float, is_fallback: bool = False) -> None:
        with self._lock:
            self._rag_rerank_latency.add(latency_ms)
            self._rag_rerank_total += 1
            if is_fallback:
                self._rag_rerank_fallback += 1

    # ── 工具执行 ──

    def record_tool_call(self, tool_name: str, duration_ms: float,
                         success: bool, is_fallback: bool = False) -> None:
        with self._lock:
            self._tool_calls[tool_name] = self._tool_calls.get(tool_name, 0) + 1
            if success:
                self._tool_success[tool_name] = self._tool_success.get(tool_name, 0) + 1
            if tool_name not in self._tool_duration:
                self._tool_duration[tool_name] = _Bucket()
            self._tool_duration[tool_name].add(duration_ms)
            if is_fallback:
                self._tool_fallback += 1

    # ── LLM ──

    def record_llm_call(self, is_error: bool = False) -> None:
        with self._lock:
            self._llm_calls += 1
            if is_error:
                self._llm_errors += 1

    def record_llm_tokens(self, input_tokens: int, output_tokens: int) -> None:
        """记录一次 LLM 调用的 token 消耗。"""
        with self._lock:
            self._llm_input_tokens += input_tokens
            self._llm_output_tokens += output_tokens

    # ── 合规 ──

    def record_compliance(self, passed: bool) -> None:
        with self._lock:
            self._compliance_total += 1
            if not passed:
                self._compliance_failed += 1

    # ── 快照 ──

    def snapshot(self) -> dict[str, Any]:
        """返回当前所有指标的完整快照。"""
        uptime = round(time.time() - self._start_time, 1)
        with self._lock:
            # 工具统计
            tool_stats: dict[str, dict[str, Any]] = {}
            for name in self._tool_calls:
                calls = self._tool_calls[name]
                success = self._tool_success.get(name, 0)
                dur = self._tool_duration.get(name, _Bucket()).stats()
                tool_stats[name] = {
                    "calls": calls,
                    "success_rate": round(success / calls, 3) if calls else 0.0,
                    "duration": dur,
                }

            total = self._request_total
            errors = self._request_errors

            return {
                "uptime_seconds": uptime,
                "start_time": time.strftime(
                    "%Y-%m-%dT%H:%M:%S",
                    time.localtime(self._start_time),
                ),

                # 请求
                "requests": {
                    "total": total,
                    "errors": errors,
                    "error_rate": round(errors / total, 4) if total else 0.0,
                    "duration": self._request_duration.stats(),
                    "ttft": self._ttft.stats(),
                },
                "pipeline": {
                    "duration": self._pipeline_duration.stats(),
                },

                # 意图
                "intent": {
                    "distribution": dict(self._intent_dist),
                    "confidence": self._intent_confidences.stats(),
                    "rewrite_triggered": self._rewrite_triggered,
                    "rewrite_total": self._rewrite_total,
                    "rewrite_rate": (round(self._rewrite_triggered / self._rewrite_total, 3)
                                     if self._rewrite_total else 0.0),
                    "human_handoff_total": self._human_handoff_total,
                    "human_handoff_rate": (round(self._human_handoff_total / total, 3)
                                           if total else 0.0),
                },

                # RAG
                "rag": {
                    "search_latency": self._rag_search_latency.stats(),
                    "rerank_latency": self._rag_rerank_latency.stats(),
                    "rerank_fallback_rate": (round(self._rag_rerank_fallback / self._rag_rerank_total, 3)
                                             if self._rag_rerank_total else 0.0),
                    "empty_result_rate": (round(self._rag_empty_results / self._rag_searches_total, 3)
                                          if self._rag_searches_total else 0.0),
                },

                # 工具
                "tools": tool_stats,
                "tool_fallback_total": self._tool_fallback,

                # LLM
                "llm": {
                    "calls": self._llm_calls,
                    "errors": self._llm_errors,
                    "error_rate": (round(self._llm_errors / self._llm_calls, 4)
                                   if self._llm_calls else 0.0),
                    "input_tokens": self._llm_input_tokens,
                    "output_tokens": self._llm_output_tokens,
                    "total_tokens": self._llm_input_tokens + self._llm_output_tokens,
                },

                # 合规
                "compliance": {
                    "total": self._compliance_total,
                    "failed": self._compliance_failed,
                    "pass_rate": (round((self._compliance_total - self._compliance_failed) / self._compliance_total, 3)
                                  if self._compliance_total else 1.0),
                },

                # 基础设施
                "infra": {
                    "memory_mb": round(_get_memory_mb(), 1),
                },
            }


# ── 全局单例 ──

collector = MetricsCollector()


# ── 辅助 ──

def _get_memory_mb() -> float:
    """获取当前进程 RSS 内存（MB）。"""
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except ImportError:
        return 0.0
