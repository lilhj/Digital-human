"""Golden Dataset 评测 harness（需求文档 §2.2/§3.2）。

两种模式：
- mock：不调真实 LLM 裁判，按「决策是否等于期望」生成确定性三维分数（离线 CI 可跑）。
- real：调 LLM-as-a-judge（app.eval.judge.judge_score）对每个用例三维打分。
输出：JSON + Markdown 报告。
"""
import asyncio
import json
import logging
from pathlib import Path

from app.core.config import get_settings
from app.eval.aggregate import aggregate_scores
from app.eval.golden_dataset import load_cases
from app.eval.report import md_file, report_file
from app.policy.decision import DecisionPolicy

logger = logging.getLogger(__name__)


def run_single(case: dict) -> dict:
    """跑一次决策策略，返回 (decision, reason)。G09 为接口层拒绝（不经策略）。"""
    if case["expected"] == "REJECT_API":
        return {"decision": "REJECT_API", "reason": "接口 422 拒绝（金额超实付）"}
    policy = DecisionPolicy()
    c = case["case"]
    d = policy.decide(
        amount_cent=c["amount_cent"],
        actual_amount_cent=c["actual_amount_cent"],
        ocr_confidence=c.get("ocr_confidence"),
        evidence_status=c.get("evidence_status", "OK"),
        fraud_score=c["fraud_score"],
        sentiment_score=c["sentiment_score"],
        errors=c.get("errors", []),
    )
    return {"decision": d.decision, "reason": d.reason}


def _decision_match(actual: str, expected: str) -> bool:
    """决策匹配：REJECT_API 视为 REJECT 判定域外的接口硬校验，单独标记。"""
    if expected == "REJECT_API":
        return actual == "REJECT_API"
    return actual == expected


def mock_judge(case: dict, actual: dict) -> dict:
    """离线确定性打分：正确 = 满分三维；错误 = 0 分理由。"""
    match = _decision_match(actual["decision"], case["expected"])
    return {
        "correctness": 5 if match else 1,
        "safety": 5 if match else 1,
        "efficiency": 3 if match else 1,
        "reason": "决策正确" if match else f"期望 {case['expected']}，实际 {actual['decision']}",
        "confidence": 1.0,
    }


async def run_benchmark(real: bool = False, judge_factory=None) -> dict:
    cases = load_cases()
    if real and judge_factory is None:
        from app.eval.judge import judge_score

        judge_factory = judge_score

    results = []
    for case in cases:
        actual = run_single(case)
        if real:
            score = await judge_factory(case, actual["decision"], case["expected"])
        else:
            score = mock_judge(case, actual)
        results.append(
            {
                "id": case["id"],
                "scenario": case["scenario"],
                "kind": case["kind"],
                "expected": case["expected"],
                "actual": actual["decision"],
                "match": _decision_match(actual["decision"], case["expected"]),
                "reason": actual["reason"],
                "score": score,
            }
        )
    return {
        "mode": "real" if real else "mock",
        "aggregate": aggregate_scores([r["score"] for r in results]),
        "cases": results,
    }


def save_results(results: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


def render_markdown(results: dict) -> str:
    lines = ["# 退赔决策 Golden Dataset 评测报告", ""]
    lines.append(f"模式：**{results['mode']}**")
    agg = results["aggregate"]
    lines.append(
        f"三维总分：决策正确性 **{agg['correctness']}** / 金额边界安全 **{agg['safety']}** / "
        f"理由质量 **{agg['efficiency']}**（综合 {agg['total']}，共 {agg['count']} 用例）"
    )
    lines.append("")
    lines.append("| # | 场景 | 期望 | 实际 | 匹配 | 正确性 | 安全性 | 理由质量 |")
    lines.append("|---|------|------|------|------|--------|--------|----------|")
    for r in results["cases"]:
        s = r["score"]
        match = "✅" if r["match"] else "❌"
        lines.append(
            f"| {r['id']} | {r['scenario']} | {r['expected']} | {r['actual']} | {match} | "
            f"{s['correctness']} | {s['safety']} | {s['efficiency']} |"
        )
    lines.append("")
    lines.append("## 逐用例理由")
    for r in results["cases"]:
        lines.append(f"- **{r['id']}** {r['scenario']}：{r['score']['reason']}")
    return "\n".join(lines)


def main(real: bool = False) -> dict:
    """命令行/脚本入口：跑评测并落盘 JSON + Markdown 报告（mock/real 分文件）。"""
    results = asyncio.run(run_benchmark(real=real))
    json_path = report_file(real)
    md_path = md_file(real)
    save_results(results, json_path)
    md_path.write_text(render_markdown(results), encoding="utf-8")
    logger.info("评测完成(%s)：%s（JSON=%s, MD=%s）", results["mode"], results["aggregate"], json_path, md_path)
    return results


if __name__ == "__main__":
    import sys

    main(real="--real" in sys.argv)
