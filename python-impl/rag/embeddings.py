# ============================================================
# Embedding 模块 — 云端 Embedding + 长度安全包装
# ============================================================

from __future__ import annotations

from typing import Any

from langchain_openai import OpenAIEmbeddings
from infra import resolve_embedding_base_url


class LengthSafeEmbeddings(OpenAIEmbeddings):
    """继承 OpenAIEmbeddings，自动截断超长文本以避免 API token 限制。

    直接继承 OpenAIEmbeddings 以满足 LangChain Chroma 对 Embeddings 类型的检查。
    """

    def __init__(self, max_chars: int = 180, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._max_chars = max_chars

    def embed_documents(
        self, texts: list[str], chunk_size: int | None = None, **kwargs: Any
    ) -> list[list[float]]:
        # 1. 截断超长文本
        safe = [t[:self._max_chars] if len(t) > self._max_chars else t for t in texts]
        # 2. 交给父类（check_embedding_ctx_length=False 确保直接发原始字符串）
        return super().embed_documents(safe, chunk_size=chunk_size, **kwargs)

    def embed_query(self, text: str, **kwargs: Any) -> list[float]:
        safe = text[:self._max_chars] if len(text) > self._max_chars else text
        return super().embed_query(safe, **kwargs)


def build_cloud_embeddings(settings) -> LengthSafeEmbeddings:
    """从配置构建云端 Embedding 实例。

    Args:
        settings: config.Settings 实例

    Returns:
        LengthSafeEmbeddings 实例（OpenAIEmbeddings 子类）
    """
    base_url = resolve_embedding_base_url(settings)
    api_key = settings.rag_embedding_api_key or settings.llm_api_key

    return LengthSafeEmbeddings(
        model=settings.rag_embedding_model,
        api_key=api_key,
        base_url=base_url,
        max_chars=settings.rag_embedding_max_chars,
        # 关键：禁用 tiktoken 切分，直接发原始字符串。
        # DashScope 等兼容网关不接受 token ID 数组格式的请求体。
        check_embedding_ctx_length=False,
    )
