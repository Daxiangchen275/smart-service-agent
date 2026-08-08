# ============================================================
# 配置管理 — 基于 Pydantic Settings 从 .env 加载配置
# ============================================================

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["deepseek", "doubao", "qwen", "openai"]

# 常见 OpenAI 兼容网关默认地址（可通过环境变量覆盖）
_DEFAULT_BASE_URLS: dict[str, str] = {
    "doubao": "https://ark.cn-beijing.volces.com/api/v3",
    "deepseek": "https://api.deepseek.com/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "openai": "https://api.openai.com/v1",
}


class Settings(BaseSettings):
    """智能客服多 Agent 系统全局配置。

    所有配置项均可通过 .env 文件或环境变量覆盖。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── 服务 ──
    host: str = Field(default="0.0.0.0", description="服务监听地址")
    port: int = Field(default=8000, description="服务监听端口")
    debug: bool = Field(default=False, description="调试模式（开启后自动重载）")
    cors_origins: str = Field(
        default="http://localhost:3000,http://localhost:8000",
        description="允许的 CORS 来源，逗号分隔"
    )

    # ── API 鉴权 ──
    api_auth_enabled: bool = Field(default=False, description="是否启用 API Key 鉴权")
    api_auth_key: str = Field(default="sk-your-api-auth-key", description="API 鉴权密钥")

    # ── LLM ──
    llm_provider: Provider = Field(default="deepseek", description="LLM 提供商：doubao | deepseek | qwen | openai")
    llm_api_key: str = Field(default="sk-your-api-key-here", description="LLM API Key")
    llm_base_url: str | None = Field(default=None, description="覆盖默认 LLM 网关地址，为空则按 provider 自动选择")
    llm_model: str = Field(default="deepseek-chat", description="LLM 模型名")
    llm_temperature: float = Field(default=0.3, ge=0.0, le=2.0, description="生成温度")
    llm_max_tokens: int = Field(default=2048, ge=1, le=65536, description="单次生成最大 token 数")

    # ── Embedding ──
    rag_embedding_base_url: str | None = Field(
        default=None, description="Embedding API Base URL（OpenAI 兼容），为空则复用 LLM 网关"
    )
    rag_embedding_api_key: str | None = Field(
        default=None, description="Embedding API Key，为空则复用 LLM_API_KEY"
    )
    rag_embedding_model: str = Field(
        default="text-embedding-v3", description="Embedding 模型名"
    )
    rag_embedding_max_chars: int = Field(
        default=180, ge=50, le=2000, description="单条文本最大字符数（超长截断）"
    )
    rag_embedding_timeout_seconds: int = Field(
        default=20, ge=3, le=300, description="Embedding API 超时秒数"
    )
    rag_embedding_max_retries: int = Field(
        default=1, ge=0, le=10, description="Embedding API 最大重试次数"
    )

    # ── Reranker ──
    rag_reranker_base_url: str | None = Field(
        default=None,
        description="Rerank API Base URL，为空则复用 LLM 网关",
        validation_alias=AliasChoices("rag_reranker_base_url", "rag_rerank_base_url"),
    )
    rag_reranker_api_key: str | None = Field(
        default=None,
        description="Rerank API Key，为空则复用 LLM_API_KEY",
        validation_alias=AliasChoices("rag_reranker_api_key", "rag_rerank_api_key"),
    )
    rag_reranker_model: str = Field(
        default="BAAI/bge-reranker-v2-m3",
        description="Rerank 模型名",
        validation_alias=AliasChoices("rag_reranker_model", "rag_rerank_model"),
    )

    # ── RAG 检索参数 ──
    rag_top_k: int = Field(default=12, ge=1, le=50, description="向量召回候选数")
    rag_top_n: int = Field(default=5, ge=1, le=20, description="重排后保留数")
    rag_chunk_size: int = Field(default=500, ge=50, le=4000, description="文档分块大小")
    rag_chunk_overlap: int = Field(default=80, ge=0, le=1000, description="分块重叠字符数")

    # ── Chroma 向量库 ──
    chroma_persist_dir: str = Field(default="./vector_store/chroma", description="Chroma 持久化目录")
    kb_dir: str = Field(default="./knowledge_base", description="知识库文件目录")

    # ── MySQL ──
    mysql_host: str = Field(default="localhost", description="MySQL 主机")
    mysql_port: int = Field(default=3306, ge=1, le=65535, description="MySQL 端口")
    mysql_user: str = Field(default="root", description="MySQL 用户")
    mysql_password: str = Field(default="", description="MySQL 密码，为空则不连接数据库")
    mysql_database: str = Field(default="smart_service", description="MySQL 数据库名")
    mysql_pool_size: int = Field(default=5, ge=1, le=50, description="MySQL 连接池大小")

    # ── 知识库启动策略 ──
    kb_build_on_startup: bool = Field(default=False, description="启动时是否自动构建 KB 索引")
    kb_seed_on_startup: bool = Field(default=True, description="索引为空时是否加载内置示例文档")

    # ── Checkpoint 持久化 ──
    checkpoint_db_path: str = Field(
        default="./checkpoints.db",
        description="LangGraph checkpoint SQLite 数据库路径，用于持久化会话状态"
    )

    # ── 短期记忆 ──
    stm_max_rounds: int = Field(default=20, ge=1, le=100, description="短期记忆最大保留轮数")
    stm_md_dir: str | None = Field(default=None, description="短期记忆 Markdown 持久化目录，为空则不启用")

    # ── 工作记忆 ──
    wm_max_records: int = Field(default=50, ge=1, le=500, description="工作记忆滑动窗口大小")

    # ── 合规 ──
    compliance_enabled: bool = Field(default=True, description="是否启用合规审查")
    compliance_sensitive_words: list[str] = Field(
        default=[
            "保证收益", "稳赚不赔", "零风险", "保本保息",
            "绝对安全", "百分百", "100%", "必涨", "内幕",
        ],
        description="合规敏感词列表",
    )

    # ── 日志 ──
    log_dir: str = Field(default="", description="文件日志目录，为空则仅输出到 stdout（生产推荐）")

    # ── 可观测性 ──
    otel_enabled: bool = Field(default=False, description="是否启用 OpenTelemetry 追踪")
    otel_endpoint: str = Field(default="http://localhost:4317", description="OTLP Exporter 端点")


@lru_cache
def get_settings() -> Settings:
    """获取 Settings 单例（带缓存）。"""
    return Settings()


# 全局单例（兼容旧导入方式）
settings = get_settings()


def resolve_llm_base_url(s: Settings | None = None) -> str:
    """解析 LLM Base URL：优先使用显式配置，否则按 provider 查默认表。"""
    if s is None:
        s = settings
    if s.llm_base_url:
        return s.llm_base_url
    return _DEFAULT_BASE_URLS.get(s.llm_provider, _DEFAULT_BASE_URLS["openai"])


def resolve_embedding_base_url(s: Settings | None = None) -> str:
    """解析 Embedding Base URL：优先 Embedding 独立配置，否则复用 LLM 网关。"""
    if s is None:
        s = settings
    if s.rag_embedding_base_url:
        return s.rag_embedding_base_url
    return resolve_llm_base_url(s)


def resolve_reranker_base_url(s: Settings | None = None) -> str:
    """解析 Reranker Base URL：优先 Reranker 独立配置，否则复用 LLM 网关。"""
    if s is None:
        s = settings
    if s.rag_reranker_base_url:
        return s.rag_reranker_base_url
    return resolve_llm_base_url(s)
