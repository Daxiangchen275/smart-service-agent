# python-impl\agents\knowledge_rag.py
# ============================================================
# KnowledgeRAG 知识检索 Agent — 仅做向量检索 + LLM 生成
# ============================================================

from __future__ import annotations

import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

import time as _time

from tracing.otel_config import trace_agent_call
from tracing.collector import collector

logger = logging.getLogger(__name__)

RAG_SYSTEM_PROMPT = """你是智能客服知识检索助手。严格遵循以下规则生成回复：

1. **基于文档**：仅基于检索到的知识库文档回答，不编造信息
2. **信息不足**：当知识库无法覆盖用户问题时，明确说明并建议转人工客服
3. **金融免责**：涉及金融产品时，必须标注"以上信息仅供参考，不构成投资建议"
4. **引用来源**：回答末尾标注引用来源（文档来源字段）
5. **语言一致**：用中文回复中文用户，用英文回复英文用户
"""

QUERY_REWRITE_PROMPT = """将以下用户口语化问题改写为更适合向量检索的简洁查询。
只返回改写后的查询文本，不要添加任何解释。

用户问题：{question}

改写查询："""


class KnowledgeRAGAgent:
    """知识检索 Agent：向量检索 + Cross-Encoder 重排 + LLM 生成。

    流水线：查询改写 → 向量召回(ChromaDB) → Cross-Encoder 重排 → LLM 生成
    """

    def __init__(self, llm: BaseChatModel, knowledge_store,
                 internal_llm: BaseChatModel | None = None) -> None:
        self._llm = llm                          # streaming LLM（用户可见回复）
        self._internal_llm = internal_llm or llm  # internal LLM（查询改写，token 可追踪）
        self._knowledge_store = knowledge_store

    @trace_agent_call("KnowledgeRAG")
    async def process(self, state: dict[str, Any]) -> dict[str, Any]:
        """LangGraph 节点入口。

        流水线：查询改写 → 向量检索+重排 → LLM 生成
        """
        messages = state.get("messages", [])

        if not messages:
            return self._empty_result()

        user_text = self._extract_text(messages[-1])

        # 1. 查询改写
        rewritten = await self.rewrite_query(user_text)

        # 2. 向量检索 + 重排（直接走 ChromaKnowledgeStore）
        documents = await self.retrieve_documents(rewritten)

        # 3. LLM 生成答案
        answer = await self.generate_answer(user_text, documents)

        # 提取文档来源名称
        doc_sources = [d.get("source", "未知") for d in documents]

        existing_sub = state.get("sub_results", {})
        return {
            "sub_results": {
                **existing_sub,
                "knowledge_rag": {
                    "agent": "knowledge_rag",
                    "query": rewritten,
                    "documents_count": len(documents),
                    "documents": doc_sources,
                    "answer": answer,
                }
            },
            "current_agent": "knowledge_rag",
        }

    async def rewrite_query(self, question: str) -> str:
        """将口语化问题改写为更适合向量检索的查询（内部调用，用 internal_llm 确保 token 统计）。"""
        try:
            prompt = QUERY_REWRITE_PROMPT.format(question=question)
            response = await self._internal_llm.ainvoke([HumanMessage(content=prompt)])
            from tracing.otel_config import capture_llm_tokens
            capture_llm_tokens(response)
            rewritten = self._extract_text(response)
            return rewritten.strip() or question
        except Exception as exc:
            logger.warning("Query rewrite failed: %s", exc)
            return question

    async def retrieve_documents(self, query: str) -> list:
        """向量召回 + Cross-Encoder 重排，直接使用 ChromaKnowledgeStore。"""
        try:
            t0 = _time.perf_counter()
            docs = await self._knowledge_store.search_and_rerank(query, top_k=5, top_n=3)
            search_ms = (_time.perf_counter() - t0) * 1000

            collector.record_rag_search(search_ms, len(docs))
            return [
                {
                    "content": d.page_content,
                    "source": d.metadata.get("source", ""),
                    "score": d.metadata.get("rerank_score", 0),
                }
                for d in docs
            ]
        except Exception as exc:
            logger.warning("Knowledge search failed: %s", exc)
            collector.record_rag_search(0, 0)
            return []

    async def generate_answer(self, question: str, documents: list[dict]) -> str:
        """基于检索到的文档，LLM 生成带引用来源的回复。"""
        # 构建文档上下文
        doc_text_parts: list[str] = []
        for i, doc in enumerate(documents, 1):
            content = doc.get("content", "")
            source = doc.get("source", "未知")
            doc_text_parts.append(f"[文档{i}] 来源:{source}\n{content}")

        doc_context = "\n\n".join(doc_text_parts) if doc_text_parts else "（未检索到相关文档）"

        user_prompt = f"""## 用户问题
{question}

## 检索到的知识库文档
{doc_context}

请基于以上信息回答用户问题。"""

        try:
            response = await self._llm.ainvoke([
                SystemMessage(content=RAG_SYSTEM_PROMPT),
                HumanMessage(content=user_prompt),
            ])
            from tracing.otel_config import capture_llm_tokens
            capture_llm_tokens(response)
            return self._extract_text(response)
        except Exception as exc:
            logger.error("RAG answer generation failed: %s", exc)
            return "抱歉，知识检索服务暂时不可用，请稍后再试或转接人工客服。"

    @staticmethod
    def _extract_text(msg: Any) -> str:
        """安全提取消息文本。"""
        return msg.content if hasattr(msg, "content") else str(msg)

    def _empty_result(self) -> dict[str, Any]:
        return {
            "sub_results": {"knowledge_rag": {"agent": "knowledge_rag", "answer": ""}},
            "current_agent": "knowledge_rag",
        }
