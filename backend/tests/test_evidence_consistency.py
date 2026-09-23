"""凭证一致性独立判定模块测试（规则优先 + LLM 兜底）。

覆盖：
1. evaluate_rule 破损声明维度全组合（声称损坏/完好/无声明 × is_damaged）；
2. 严重度夸大检测（描述强损伤词 + VL severity=轻微）；
3. penalty 映射（MATCH/PARTIAL/MISMATCH/UNCERTAIN）；
4. merge_with_llm 兜底（规则 UNCERTAIN 时才用 LLM，规则判定权威不可被改判）。
"""
from app.policy.evidence_consistency import (
    MATCH,
    MISMATCH,
    PARTIAL,
    UNCERTAIN,
    EvidenceConsistencyResult,
    evaluate_rule,
    merge_with_llm,
    penalty_of,
)


# ---------- 破损声明维度（规则层确定性比对） ----------


def test_claim_damage_and_damaged_is_match():
    """声称损坏 + 图片确实损坏 -> MATCH（0 惩罚，放行常规风控）。"""
    r = evaluate_rule(description="手机屏幕碎裂", is_damaged=True, severity="严重")
    assert r.level == MATCH
    assert r.penalty == 0.0
    assert r.dimensions == []
    assert r.source == "rule"


def test_claim_damage_but_intact_is_mismatch():
    """声称损坏 + 图片完好 -> MISMATCH（+30，薅羊毛强信号），带可解释维度。"""
    r = evaluate_rule(description="商品碎了", is_damaged=False, severity="")
    assert r.level == MISMATCH
    assert abs(r.penalty - 0.30) < 1e-9
    assert any("破损声明矛盾" in d for d in r.dimensions)


def test_claim_intact_and_intact_is_match():
    """都说完好（'商品完好，全额退款'归 fraud 判，一致性上一致）-> MATCH。"""
    r = evaluate_rule(description="商品完好，需要全额退款", is_damaged=False, severity="")
    assert r.level == MATCH
    assert r.penalty == 0.0


def test_claim_intact_but_damaged_is_partial():
    """说完好但图片有损 -> PARTIAL（图比描述严重，非薅羊毛方向）。"""
    r = evaluate_rule(description="商品完好无损", is_damaged=True, severity="")
    assert r.level == PARTIAL
    assert abs(r.penalty - 0.15) < 1e-9


def test_no_claim_but_damaged_is_partial():
    """图片有损但描述无状态主张（如'不想要了'）-> PARTIAL，人工确认诉求。"""
    r = evaluate_rule(description="不想要了", is_damaged=True, severity="")
    assert r.level == PARTIAL
    assert abs(r.penalty - 0.15) < 1e-9


def test_no_claim_and_intact_is_uncertain():
    """图片完好 + 描述无状态声明 -> UNCERTAIN（0 惩罚，信息不足）。"""
    r = evaluate_rule(description="不想要了", is_damaged=False, severity="")
    assert r.level == UNCERTAIN
    assert r.penalty == 0.0


def test_unknown_damage_state_is_uncertain():
    """VL 未给出 is_damaged（Ollama 降级等）-> UNCERTAIN，交由 LLM 兜底。"""
    r = evaluate_rule(description="商品碎了", is_damaged=None, severity="")
    assert r.level == UNCERTAIN


# ---------- 严重度夸大（轻量维度） ----------


def test_severity_exaggeration_is_partial():
    """描述强损伤词（'严重'）+ VL severity=轻微 -> PARTIAL（损伤描述可能夸大）。"""
    r = evaluate_rule(description="商品严重损坏", is_damaged=True, severity="轻微")
    assert r.level == PARTIAL
    assert any("夸大" in d for d in r.dimensions)


def test_no_severity_no_exaggeration():
    """severity 为空时不触发夸大检测。"""
    r = evaluate_rule(description="商品严重损坏", is_damaged=True, severity="")
    assert r.level == MATCH


# ---------- penalty 映射 ----------


def test_penalty_mapping():
    assert penalty_of(MATCH) == 0.0
    assert abs(penalty_of(PARTIAL) - 0.15) < 1e-9
    assert abs(penalty_of(MISMATCH) - 0.30) < 1e-9
    assert penalty_of(UNCERTAIN) == 0.0
    assert penalty_of("UNKNOWN") == 0.0


# ---------- merge_with_llm：规则优先，仅 UNCERTAIN 用 LLM 兜底 ----------


def test_merge_uncertain_with_llm_inconsistent():
    """规则判不了（UNCERTAIN）+ LLM 兜底 inconsistent -> MISMATCH（LLM 兜底）。"""
    rule = evaluate_rule(description="不想要了", is_damaged=False, severity="")
    merged = merge_with_llm(rule, "inconsistent")
    assert merged.level == MISMATCH
    assert merged.source == "llm"
    assert abs(merged.penalty - 0.30) < 1e-9


def test_merge_uncertain_with_llm_consistent():
    rule = evaluate_rule(description="不想要了", is_damaged=False, severity="")
    merged = merge_with_llm(rule, "consistent")
    assert merged.level == MATCH
    assert merged.source == "llm"


def test_merge_rule_authoritative_over_llm():
    """规则已判 MATCH/MISMATCH 时 LLM 不改判（规则是确定性权威，避免 LLM 误判再污染）。"""
    rule = evaluate_rule(description="商品碎了", is_damaged=False, severity="")  # MISMATCH
    merged = merge_with_llm(rule, "consistent")
    assert merged.level == MISMATCH
    assert merged.source == "rule"
