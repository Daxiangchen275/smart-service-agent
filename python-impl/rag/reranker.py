# ============================================================
# Reranker 模块 — Cross-Encoder 重排 + Jaccard 降级
# ============================================================

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ApiReranker:
    """调用 OpenAI 兼容 /rerank 端点的 Cross-Encoder 远程重排器。

    API 不可用时自动降级为 Jaccard 词重叠评分。
    """

    def __init__(self, base_url: str, api_key: str, model: str = "BAAI/bge-reranker-v2-m3") -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model

    async def rerank(self, query: str, documents: list[str], top_n: int = 5) -> list[dict[str, Any]]:
        """对候选文档打分排序，返回 top_n。

        Returns:
            [{"index": int, "score": float, "text": str}, ...]
        """
        import aiohttp

        is_dashscope = "dashscope" in self._base_url.lower()

        if is_dashscope:
            # DashScope Rerank API: 独立路径 + 不同的请求/响应格式
            url = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self._model,
                "input": {
                    "query": query,
                    "documents": documents,
                },
                "parameters": {"top_n": top_n},
            }
        else:
            url = f"{self._base_url}/rerank"
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self._model,
                "query": query,
                "documents": documents,
                "top_n": top_n,
            }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers,
                                        timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # DashScope: {"output": {"results": [...]}}
                        # OpenAI-compatible: {"results": [...]}
                        if is_dashscope:
                            raw_results = data.get("output", {}).get("results", [])
                            return self._normalize_dashscope_results(raw_results)
                        return data.get("results", [])
                    else:
                        logger.warning("Reranker API returned %d, falling back to Jaccard", resp.status)
        except Exception as exc:
            logger.warning("Reranker API unavailable (%s), falling back to Jaccard", exc)

        return self._jaccard_rerank(query, documents, top_n)

    @staticmethod
    def _normalize_dashscope_results(raw: list[dict]) -> list[dict[str, Any]]:
        """将 DashScope 响应格式统一为 {"index", "score", "text"}。

        DashScope 不同模型返回格式略有差异：
        - {"index": 0, "relevance_score": 0.9, "document": {"text": "..."}}
        - {"index": 0, "document": {"text": "...", "relevance_score": 0.9}}
        """
        normalized = []
        for r in raw:
            idx = r.get("index", 0)
            score = r.get("relevance_score", 0.0)
            text = ""
            doc = r.get("document", {})
            if isinstance(doc, dict):
                text = doc.get("text", "")
                # relevance_score 可能在 document 内
                if score == 0.0:
                    score = doc.get("relevance_score", 0.0)
            normalized.append({"index": idx, "score": score, "text": text})
        return normalized

    @staticmethod
    def _jaccard_rerank(query: str, documents: list[str], top_n: int) -> list[dict[str, Any]]:
        """Jaccard 词重叠降级方案。"""
        query_tokens = set(query.lower().split())
        scored = []
        for idx, doc in enumerate(documents):
            doc_tokens = set(doc.lower().split())
            if not query_tokens and not doc_tokens:
                score = 0.0
            elif not query_tokens or not doc_tokens:
                score = 0.0
            else:
                intersection = query_tokens & doc_tokens
                union = query_tokens | doc_tokens
                score = len(intersection) / len(union) if union else 0.0
            scored.append((idx, score, doc))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [{"index": idx, "score": s, "text": txt} for idx, s, txt in scored[:top_n]]


async def rerank_documents(
    reranker: ApiReranker,
    query: str,
    documents: list,
    top_n: int = 5,
) -> list:
    """对 LangChain Document 列表进行重排，保留 top_n，写入 rerank_score 元数据。

    Args:
        reranker: ApiReranker 实例
        query: 查询文本
        documents: LangChain Document 列表
        top_n: 保留数量

    Returns:
        重排后的 Document 列表（带 rerank_score 元数据）
    """
    if not documents:
        return documents

    texts = [d.page_content for d in documents]
    results = await reranker.rerank(query, texts, top_n=top_n)

    reranked = []
    for r in results:
        idx = r["index"]
        if idx < len(documents):
            doc = documents[idx]
            doc.metadata["rerank_score"] = r["score"]
            reranked.append(doc)

    return reranked
