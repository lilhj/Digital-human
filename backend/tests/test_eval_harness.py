"""评测 harness 测试（工单5 移植）：Golden Dataset + LLM-as-judge 三维评分。

- BENCH_CASES == 10（覆盖退赔全部业务边界）
- mock 模式跑通 + 决策匹配断言（G01 小额秒退 / G02 超300挂起 / G03 高风险拒绝 / G09 金额超实付）
- 三维评分聚合
"""
import pytest

from app.eval.aggregate import aggregate_scores
from app.eval.golden_dataset import load_cases
from app.eval.harness import (
    _decision_match,
    mock_judge,
    render_markdown,
    run_benchmark,
    run_single,
)


def test_bench_cases_are_10():
    cases = load_cases()
    assert len(cases) == 10
    ids = [c["id"] for c in cases]
    assert len(set(ids)) == 10


def test_golden_dataset_covers_boundaries():
    """10 个用例覆盖退赔全部业务边界（需求文档 §2.2 表）。"""
    scenarios = {c["scenario"] for c in load_cases()}
    for s in (
        "小额低风险",
        "超300元限额",
        "高风险恶意退款",
        "OCR低置信",
        "OCR中等置信",
        "舆情升级HIGH",
        "中风险区间",
        "空凭证",
        "金额超实付",
        "正常小额退货",
    ):
        assert s in scenarios, f"缺少场景 {s}"


def test_run_single_approve_low_risk():
    """G01：128 元低风险 -> APPROVE（秒退）。"""
    case = next(c for c in load_cases() if c["id"] == "G01")
    actual = run_single(case)
    assert actual["decision"] == "APPROVE"


def test_run_single_over_limit_suspends():
    """G02：350 元超限额 -> HUMAN_REVIEW（挂起）。"""
    case = next(c for c in load_cases() if c["id"] == "G02")
    actual = run_single(case)
    assert actual["decision"] == "HUMAN_REVIEW"


def test_run_single_high_risk_rejects():
    """G03：风险分 0.9 -> REJECT。"""
    case = next(c for c in load_cases() if c["id"] == "G03")
    actual = run_single(case)
    assert actual["decision"] == "REJECT"


def test_run_single_amount_exceeds_api():
    """G09：申请额 > 实付 -> 接口层 REJECT_API（不经策略，硬校验）。"""
    case = next(c for c in load_cases() if c["id"] == "G09")
    actual = run_single(case)
    assert actual["decision"] == "REJECT_API"


def test_mock_judge_scores():
    case = next(c for c in load_cases() if c["id"] == "G01")
    score = mock_judge(case, {"decision": "APPROVE", "reason": "低风险自动通过"})
    assert score["correctness"] == 5
    assert score["safety"] == 5
    assert score["confidence"] == 1.0


@pytest.mark.asyncio
async def test_run_benchmark_mock():
    results = await run_benchmark(real=False)
    assert results["mode"] == "mock"
    assert len(results["cases"]) == 10
    assert all(r["match"] for r in results["cases"]), (
        [f"{r['id']}:期望{r['expected']}实际{r['actual']}" for r in results["cases"] if not r["match"]]
    )
    agg = results["aggregate"]
    assert agg["count"] == 10
    assert agg["total"] > 4.0  # 全对 -> 高分（G09 接口硬校验另算）


def test_aggregate_scores():
    results = [
        {"correctness": 5, "safety": 5, "efficiency": 5},
        {"correctness": 1, "safety": 1, "efficiency": 1},
    ]
    agg = aggregate_scores(results)
    assert agg["total"] == 3.0
    assert agg["count"] == 2


def test_render_markdown():
    import asyncio

    results = asyncio.run(run_benchmark(real=False))
    md = render_markdown(results)
    assert "评测报告" in md
    assert "G01" in md
    assert "✅" in md


def test_decision_match():
    assert _decision_match("APPROVE", "APPROVE") is True
    assert _decision_match("REJECT", "APPROVE") is False
    assert _decision_match("REJECT_API", "REJECT_API") is True
