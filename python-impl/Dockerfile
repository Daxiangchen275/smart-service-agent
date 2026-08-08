# ============================================================
# 智能客服多 Agent 系统 - Docker 镜像
# 构建: docker build -t smart-service-agent .
# 运行: docker run -p 8000:8000 --env-file .env smart-service-agent
# ============================================================

FROM python:3.11-slim

WORKDIR /app

# 安装 Python 依赖（利用 Docker 缓存层）
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 创建非 root 用户并设置目录权限
RUN groupadd -r appuser && useradd -r -g appuser appuser \
    && mkdir -p /app/vector_store/chroma /app/conversations /app/logs \
    && chown -R appuser:appuser /app

USER appuser

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

EXPOSE 8000

# 生产模式: 单 worker（LangGraph MemorySaver 不支持多进程共享）
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--log-level", "info"]
