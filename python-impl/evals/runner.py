# python-impl\evals\runner.py
# ============================================================
# 评测执行器：调 API → 收集结果 → 计算指标 → 打印报告
# ============================================================
"""
用法：
    # 全部评测
    python evals/runner.py

    # 只评测意图分类
    python evals/runner.py --suite intent

    # 评测 + 输出 JSON 报告
    python evals/runner.py --json report.json
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from evals.metrics import (
    accuracy,
    entity_f1,
    keyword_coverage,
    calibration_ece,
)
from evals.test_cases import INTENT_CASES, ENTITY_CASES, RAG_CASES, E2E_CASES


@dataclass
class EvalReport:
    """评测报告。"""
    suite: str = ""
    total: int = 0
    passed: int = 0
    accuracy: float = 0.0
    entity_f1: float = 0.0
    keyword_coverage: float = 0.0
    ece: float = 0.0
    duration_seconds: float = 0.0
    details: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class EvalRunner:
    """评测执行器。"""

    def __init__(self, base_url: str = "http://127.0.0.1:8000",
                 timeout: int = 60) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout)

    # ── 意图分类评测 ──

    def eval_intent(self) -> EvalReport:
        """评测意图分类准确率。"""
        report = EvalReport(suite="intent")
        confidences: list[float] = []
        correct_flags: list[bool] = []

        for user_msg, expected_intent, expected_agent in INTENT_CASES:
            try:
                data = self._chat(user_msg)
                ir = data.get("intent_result", {})
                pred_intent = ir.get("primary_intent", "")
                pred_agent = ir.get("suggested_agent", "")
                confidence = ir.get("confidence", 0)

                intent_ok = pred_intent == expected_intent
                agent_ok = pred_agent == expected_agent
                passed = intent_ok and agent_ok

                confidences.append(confidence)
                correct_flags.append(passed)

                report.details.append({
                    "case": user_msg,
                    "expected_intent": expected_intent,
                    "predicted_intent": pred_intent,
                    "expected_agent": expected_agent,
                    "predicted_agent": pred_agent,
                    "confidence": confidence,
                    "intent_ok": intent_ok,
                    "agent_ok": agent_ok,
                    "passed": passed,
                })
            except Exception as exc:
                report.errors.append(f"[intent] {user_msg}: {exc}")
                report.details.append({
                    "case": user_msg,
                    "error": str(exc),
                    "passed": False,
                })

        report.total = len(INTENT_CASES)
        report.passed = sum(1 for d in report.details if d.get("passed"))
        report.accuracy = accuracy(report.passed, report.total)
        report.ece = calibration_ece(confidences, correct_flags)
        return report

    # ── 实体提取评测 ──

    def eval_entity(self) -> EvalReport:
        """评测实体提取 F1。"""
        report = EvalReport(suite="entity")
        all_expected: dict[str, str] = {}
        all_predicted: dict[str, str] = {}

        for user_msg, expected_entities in ENTITY_CASES:
            try:
                data = self._chat(user_msg)
                ir = data.get("intent_result", {})
                pred_entities = ir.get("entities", {})

                case_passed = True
                for k, v in expected_entities.items():
                    if pred_entities.get(k, "").upper() != v.upper():
                        case_passed = False
                        break

                all_expected.update({f"{user_msg}:{k}": v for k, v in expected_entities.items()})
                all_predicted.update({f"{user_msg}:{k}": pred_entities.get(k, "")
                                     for k in expected_entities})

                report.details.append({
                    "case": user_msg,
                    "expected_entities": expected_entities,
                    "predicted_entities": pred_entities,
                    "passed": case_passed,
                })
            except Exception as exc:
                report.errors.append(f"[entity] {user_msg}: {exc}")

        report.total = len(ENTITY_CASES)
        report.passed = sum(1 for d in report.details if d.get("passed"))
        report.entity_f1 = entity_f1(all_expected, all_predicted)
        return report

    # ── RAG 检索评测 ──

    def eval_rag(self) -> EvalReport:
        """评测 RAG 回复的关键词覆盖率。"""
        report = EvalReport(suite="rag")
        coverages: list[float] = []

        for user_msg, expected_keywords in RAG_CASES:
            try:
                data = self._chat(user_msg)
                reply = data.get("response", "")
                cov = keyword_coverage(reply, expected_keywords)
                coverages.append(cov)

                report.details.append({
                    "case": user_msg,
                    "expected_keywords": expected_keywords,
                    "reply": reply[:300],
                    "keyword_coverage": cov,
                    "passed": cov >= 0.6,
                })
            except Exception as exc:
                report.errors.append(f"[rag] {user_msg}: {exc}")

        report.total = len(RAG_CASES)
        report.passed = sum(1 for d in report.details if d.get("passed"))
        report.keyword_coverage = (sum(coverages) / len(coverages)
                                   if coverages else 0.0)
        return report

    # ── 端到端评测 ──

    def eval_e2e(self) -> EvalReport:
        """端到端回复质量评测：必须包含关键词 + 不含禁止词。"""
        report = EvalReport(suite="e2e")

        for user_msg, required, forbidden in E2E_CASES:
            try:
                data = self._chat(user_msg)
                reply = data.get("response", "")

                has_required = keyword_coverage(reply, required) >= 0.6
                no_forbidden = all(fw not in reply for fw in forbidden)
                passed = has_required and no_forbidden

                report.details.append({
                    "case": user_msg,
                    "required_keywords": required,
                    "forbidden_keywords": forbidden,
                    "reply": reply[:300],
                    "has_required": has_required,
                    "no_forbidden": no_forbidden,
                    "passed": passed,
                })
            except Exception as exc:
                report.errors.append(f"[e2e] {user_msg}: {exc}")

        report.total = len(E2E_CASES)
        report.passed = sum(1 for d in report.details if d.get("passed"))
        report.accuracy = accuracy(report.passed, report.total)
        return report

    # ── HTTP 调用 ──

    def _chat(self, message: str,
              user_id: str = "eval-user",
              session_id: str = "eval-session") -> dict[str, Any]:
        """发送聊天请求，返回 JSON 响应。"""
        resp = self._client.post(
            f"{self._base_url}/api/chat",
            json={
                "message": message,
                "user_id": user_id,
                "session_id": session_id,
            },
        )
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        self._client.close()


# ── 报告打印 ──

def _print_header(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def _print_report(report: EvalReport) -> None:
    _print_header(f"评测: {report.suite}")
    print(f"  用例数:    {report.total}")
    print(f"  通过:      {report.passed}")
    if report.suite == "intent":
        print(f"  准确率:    {report.accuracy:.1%}")
        print(f"  ECE:       {report.ece:.4f}")
    elif report.suite == "entity":
        print(f"  实体F1:    {report.entity_f1:.1%}")
    elif report.suite == "rag":
        print(f"  关键词覆盖: {report.keyword_coverage:.1%}")
    elif report.suite == "e2e":
        print(f"  通过率:    {report.accuracy:.1%}")

    # 打印失败用例
    failures = [d for d in report.details if not d.get("passed")]
    if failures:
        print(f"\n  失败用例 ({len(failures)}):")
        for f in failures:
            case = f.get("case", "?")
            print(f"    - {case}")
            if "expected_intent" in f:
                print(f"      期望意图={f['expected_intent']}, 实际={f.get('predicted_intent','?')}")
            if "expected_entities" in f:
                print(f"      期望实体={f['expected_entities']}, 实际={f.get('predicted_entities','?')}")
            if "expected_keywords" in f:
                print(f"      期望关键词={f['expected_keywords']}, 覆盖={f.get('keyword_coverage','?')}")

    if report.errors:
        print(f"\n  错误 ({len(report.errors)}):")
        for e in report.errors:
            print(f"    - {e}")

    print(f"  耗时:      {report.duration_seconds:.1f}s")


# ── 入口 ──

def run_evals(base_url: str = "http://127.0.0.1:8000",
               suites: list[str] | None = None,
               json_output: str | None = None) -> None:
    """运行评测并打印报告。

    Args:
        base_url: 服务地址
        suites: 评测套件列表，None 表示运行全部
        json_output: JSON 报告输出路径
    """
    if suites is None:
        suites = ["intent", "entity", "rag", "e2e"]

    runner = EvalRunner(base_url)
    all_reports: dict[str, Any] = {}
    overall_start = time.perf_counter()

    try:
        suite_map = {
            "intent": runner.eval_intent,
            "entity": runner.eval_entity,
            "rag": runner.eval_rag,
            "e2e": runner.eval_e2e,
        }

        for suite_name in suites:
            if suite_name not in suite_map:
                print(f"未知评测套件: {suite_name}")
                continue

            start = time.perf_counter()
            report = suite_map[suite_name]()
            report.duration_seconds = round(time.perf_counter() - start, 1)
            _print_report(report)
            all_reports[suite_name] = report

        # 总结
        _print_header("总结")
        total_cases = sum(r.total for r in all_reports.values())
        total_passed = sum(r.passed for r in all_reports.values())
        total_time = round(time.perf_counter() - overall_start, 1)
        print(f"  总用例: {total_cases}")
        print(f"  总通过: {total_passed}")
        print(f"  总通过率: {total_passed/total_cases:.1%}" if total_cases else "N/A")
        print(f"  总耗时: {total_time}s")

        # JSON 输出
        if json_output:
            output = {
                "summary": {
                    "total_cases": total_cases,
                    "total_passed": total_passed,
                    "pass_rate": total_passed / total_cases if total_cases else 0,
                    "duration_seconds": total_time,
                },
                "suites": {},
            }
            for name, report in all_reports.items():
                output["suites"][name] = {
                    "total": report.total,
                    "passed": report.passed,
                    "accuracy": report.accuracy,
                    "entity_f1": report.entity_f1,
                    "keyword_coverage": report.keyword_coverage,
                    "ece": report.ece,
                    "duration_seconds": report.duration_seconds,
                    "details": report.details,
                    "errors": report.errors,
                }
            with open(json_output, "w", encoding="utf-8") as f:
                json.dump(output, f, ensure_ascii=False, indent=2)
            print(f"\n  JSON 报告已保存: {json_output}")

    finally:
        runner.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="智能客服评测系统")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000",
                       help="服务地址 (默认 http://127.0.0.1:8000)")
    parser.add_argument("--suite", nargs="+",
                       choices=["intent", "entity", "rag", "e2e"],
                       help="评测套件（可多选），默认全部")
    parser.add_argument("--json", dest="json_output",
                       help="JSON 报告输出路径")
    args = parser.parse_args()

    run_evals(
        base_url=args.base_url,
        suites=args.suite,
        json_output=args.json_output,
    )
