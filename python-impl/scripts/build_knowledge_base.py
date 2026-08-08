#!/usr/bin/env python3
# ============================================================
# 知识库构建 CLI 脚本
# ============================================================
"""
用法：
    python scripts/build_knowledge_base.py --dir ./knowledge_base
    python scripts/build_knowledge_base.py --csv data/bitext.csv --clear
    python scripts/build_knowledge_base.py --jsonl data/ecommerce.jsonl

将外部数据集（CSV/JSONL）或 .txt 目录转换为 Chroma 向量索引。
"""

from __future__ import annotations

import sys
import os
import asyncio
import argparse
import csv
import json
import logging
from pathlib import Path

# 确保项目根在 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from infra import settings
from memory.chroma_store import ChromaKnowledgeStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("build_kb")


def parse_csv(filepath: str) -> tuple[list[str], list[dict]]:
    """解析 CSV 文件。期望列: text 或 content, source（可选）, category（可选）"""
    texts, metadatas = [], []
    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            text = row.get("text") or row.get("content") or ""
            if not text.strip():
                continue
            texts.append(text.strip())
            metadatas.append({
                "source": row.get("source", Path(filepath).stem),
                "category": row.get("category", ""),
            })
    return texts, metadatas


def parse_jsonl(filepath: str) -> tuple[list[str], list[dict]]:
    """解析 JSONL 文件。每行一个 JSON 对象，字段: text/content, source, category"""
    texts, metadatas = [], []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                text = obj.get("text") or obj.get("content") or ""
                if not text.strip():
                    continue
                texts.append(text.strip())
                metadatas.append({
                    "source": obj.get("source", Path(filepath).stem),
                    "category": obj.get("category", ""),
                })
            except json.JSONDecodeError:
                logger.warning("Skipping invalid JSON line: %s", line[:80])
    return texts, metadatas


async def main():
    parser = argparse.ArgumentParser(description="构建智能客服知识库索引")
    parser.add_argument("--csv", help="CSV 文件路径")
    parser.add_argument("--jsonl", help="JSONL 文件路径")
    parser.add_argument("--dir", help="知识库目录（.txt 文件）", default=settings.kb_dir)
    parser.add_argument("--clear", action="store_true", help="清除已存在的索引后重建")
    args = parser.parse_args()

    # 初始化
    store = ChromaKnowledgeStore(settings)
    logger.info("ChromaKnowledgeStore initialized: %d existing documents", store.document_count)

    if args.clear:
        logger.info("Clearing existing collection...")
        # Chroma 删除需要重建
        import shutil
        persist_dir = settings.chroma_persist_dir
        if os.path.exists(persist_dir):
            shutil.rmtree(persist_dir)
            logger.info("Removed %s", persist_dir)
        store = ChromaKnowledgeStore(settings)

    total = 0

    # CSV 加载
    if args.csv:
        logger.info("Loading CSV: %s", args.csv)
        texts, metadatas = parse_csv(args.csv)
        for text, meta in zip(texts, metadatas):
            await store.add_document(text, meta.get("source", ""), meta)
        total += len(texts)
        logger.info("Loaded %d documents from CSV", len(texts))

    # JSONL 加载
    if args.jsonl:
        logger.info("Loading JSONL: %s", args.jsonl)
        texts, metadatas = parse_jsonl(args.jsonl)
        for text, meta in zip(texts, metadatas):
            await store.add_document(text, meta.get("source", ""), meta)
        total += len(texts)
        logger.info("Loaded %d documents from JSONL", len(texts))

    # 目录加载（.txt）
    if not args.csv and not args.jsonl:
        logger.info("Loading knowledge base from directory: %s", args.dir)
        count = await store.load_knowledge_base(args.dir)
        total += count

    logger.info("Done! Total documents indexed: %d", total)


if __name__ == "__main__":
    asyncio.run(main())
