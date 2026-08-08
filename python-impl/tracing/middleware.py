# ============================================================
# 监控中间件 — 请求级指标 + Pipeline 耗时
# ============================================================

from __future__ import annotations

import time
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from tracing.collector import collector

logger = logging.getLogger(__name__)


class MonitoringMiddleware(BaseHTTPMiddleware):
    """FastAPI 中间件：自动采集请求级指标。

    记录每个 /api/* 请求的延迟和错误。
    跳过 /health /ready /metrics 等非业务端点。
    """

    _SKIP_PATHS = {"/health", "/ready", "/api/metrics", "/docs", "/openapi.json"}

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self._SKIP_PATHS or request.url.path.startswith("/static"):
            return await call_next(request)

        start = time.perf_counter()
        is_error = False
        try:
            response = await call_next(request)
            if response.status_code >= 500:
                is_error = True
            return response
        except Exception:
            is_error = True
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            collector.record_request(duration_ms, is_error)
