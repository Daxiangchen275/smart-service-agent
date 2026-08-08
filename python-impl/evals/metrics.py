# python-impl\evals\metrics.py
# ============================================================
# 评测指标：分类 / 实体 / 检索 / 文本质量
# ============================================================

from __future__ import annotations

import math
import re
from typing import Sequence


# ── 分类指标 ──

def accuracy(correct: int, total: int) -> float:
    """准确率 = 正确数 / 总数。"""
    return correct / total if total else 0.0


def precision_recall_f1(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    """精确率、召回率、F1。"""
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def macro_f1(per_class_f1: list[float]) -> float:
    """各类别 F1 的算术平均。"""
    return sum(per_class_f1) / len(per_class_f1) if per_class_f1 else 0.0


# ── 集合/实体指标 ──

def set_f1(expected: set[str], predicted: set[str]) -> float:
    """两个 token 集合的 F1（用于关键词覆盖评估）。"""
    if not expected and not predicted:
        return 1.0
    tp = len(expected & predicted)
    _, _, f1 = precision_recall_f1(tp, len(predicted - expected), len(expected - predicted))
    return f1


def entity_f1(expected: dict[str, str], predicted: dict[str, str]) -> float:
    """实体提取 F1：键值完全匹配（大小写不敏感）。"""
    if not expected and not predicted:
        return 1.0
    tp = sum(1 for k, v in expected.items()
             if predicted.get(k, "").upper() == v.upper())
    fp = len(predicted) - tp
    fn = len(expected) - tp
    _, _, f1 = precision_recall_f1(tp, fp, fn)
    return f1


# ── 检索指标 ──

def recall_at_k(ranked_ids: Sequence[str], gold_id: str, k: int) -> float:
    """Recall@K：正确答案是否在前 K 个结果中。"""
    return 1.0 if gold_id in ranked_ids[:k] else 0.0


def mrr_at_k(ranked_ids: Sequence[str], gold_id: str, k: int) -> float:
    """MRR@K：正确答案的倒数排名均值。"""
    for i, doc_id in enumerate(ranked_ids[:k]):
        if doc_id == gold_id:
            return 1.0 / (i + 1)
    return 0.0


def ndcg_at_k(relevances: Sequence[float], k: int) -> float:
    """NDCG@K：归一化折损累计增益。"""
    rels = list(relevances[:k])
    if not rels:
        return 0.0

    def dcg(scores: Sequence[float]) -> float:
        return sum(s / math.log2(i + 2) for i, s in enumerate(scores))

    ideal = sorted(relevances, reverse=True)[:k]
    idcg = dcg(ideal)
    return dcg(rels) / idcg if idcg else 0.0


# ── 文本质量指标 ──

def keyword_coverage(text: str, keywords: list[str]) -> float:
    """关键信息覆盖率：keywords 中有多少出现在 text 中。"""
    if not keywords:
        return 1.0
    text_lower = text.lower()
    hits = sum(1 for kw in keywords if kw.lower() in text_lower)
    return hits / len(keywords)


def token_overlap_f1(reference: str, hypothesis: str) -> float:
    """词袋 F1：文本与参考答案的 token 重叠度（无 embedding 时的 faithfulness 代理）。"""
    ref_tokens = set(_tokenize(reference))
    hyp_tokens = set(_tokenize(hypothesis))
    if not ref_tokens and not hyp_tokens:
        return 1.0
    tp = len(ref_tokens & hyp_tokens)
    _, _, f1 = precision_recall_f1(tp, len(hyp_tokens - ref_tokens), len(ref_tokens - hyp_tokens))
    return f1


def calibration_ece(confidences: list[float], correct_flags: list[bool],
                    n_bins: int = 10) -> float:
    """Expected Calibration Error：置信度校准误差，越小越好。"""
    if not confidences:
        return 0.0
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    for conf, ok in zip(confidences, correct_flags, strict=False):
        idx = min(int(conf * n_bins), n_bins - 1)
        bins[idx].append((conf, ok))

    ece = 0.0
    total = len(confidences)
    for bucket in bins:
        if not bucket:
            continue
        avg_conf = sum(c for c, _ in bucket) / len(bucket)
        avg_acc = sum(1 for _, ok in bucket if ok) / len(bucket)
        ece += (len(bucket) / total) * abs(avg_conf - avg_acc)
    return ece


# ── 智能客服专用指标 ──

def matches_expected(actual: str, expected: str) -> bool:
    """预期响应匹配：actual 包含 expected 的所有关键 token。"""
    return _tokenize(expected).issubset(_tokenize(actual))


# ── 工具函数 ──

def _tokenize(text: str) -> set[str]:
    """中文/英文混合分词。"""
    # 提取中文字符和英文单词
    tokens: set[str] = set()
    # 中文逐字 + 2-gram
    chinese = re.findall(r'[一-鿿]+', text.lower())  # CJK统一表意文字（完整20,992码位）
    for seg in chinese:
        tokens.update(seg)  # 单字
        tokens.update(seg[i:i + 2] for i in range(len(seg) - 1))  # bigram
    # 英文单词
    english = re.findall(r'[a-z0-9]+', text.lower())
    tokens.update(english)
    return tokens
