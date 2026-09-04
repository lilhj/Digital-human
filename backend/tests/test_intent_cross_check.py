"""工单8 交叉校验单测：apply_declared_claim（用户显式选择为主通道，冲突转人工）。

覆盖用户描述的 bug：前端默认「退款」+ 描述写「想换货」→ 规则层先命中「退款」跳过 LLM。
修复后前端无默认值且 type 独立传 claim_type；本函数保证：声明与文本识别冲突 → 转人工。
"""
from app.intent import (
    REFUND,
    RETURN,
    EXCHANGE,
    UNKNOWN,
    IntentResult,
    IntentType,
    RuleBasedIntentProvider,
    apply_declared_claim,
)


def _rule(desc: str) -> IntentResult:
    return RuleBasedIntentProvider().classify(desc)


def test_no_claim_keeps_original():
    """无声明类型（员工侧建单/旧案件）→ 双层识别结果原样返回。"""
    r = _rule("想换货")
    out = apply_declared_claim(r, None)
    assert out is r
    out2 = apply_declared_claim(r, "")
    assert out2 is r


def test_declared_matches_text():
    """用户显式选择「换货」+ 描述「想换货」→ 一致采信，source=declared。"""
    r = _rule("想换个颜色")
    out = apply_declared_claim(r, "换货")
    assert out.intent == IntentType.EXCHANGE
    assert out.source == "declared"
    assert not out.declared_conflict
    assert out.needs_human_review is True  # EXCHANGE 本就转人工，走 EXCHANGE 分支


def test_conflict_refund_declared_exchange_text():
    """关键 bug 场景：用户选「退款」但描述写「想换货」→ 冲突转人工（资损零容忍）。"""
    r = _rule("想换货")
    assert r.intent == IntentType.EXCHANGE  # 纯文本可被规则识别为换货
    out = apply_declared_claim(r, "退款")
    assert out.intent == IntentType.REFUND  # 保留用户主张
    assert out.declared_conflict is True
    assert out.needs_human_review is True  # 冲突 → 转人工
    assert "冲突" in out.reason and "EXCHANGE" in out.reason


def test_declared_rescues_unknown():
    """文本未识别出意图 + 用户显式选择 → 采信用户选择（主通道原则）。"""
    r = IntentResult(IntentType.UNKNOWN, "fallback", 0.0, "规则与 LLM 均未判定")
    out = apply_declared_claim(r, "退货退款")
    assert out.intent == IntentType.RETURN
    assert out.source == "declared"
    assert not out.declared_conflict


def test_declared_refund_text_refund():
    """用户选「退款」+ 描述「退钱」→ 一致采信，非冲突。"""
    r = _rule("快递丢件，把钱退我")
    assert r.intent == IntentType.REFUND
    out = apply_declared_claim(r, "退款")
    assert out.intent == IntentType.REFUND
    assert not out.declared_conflict


def test_invalid_claim_ignored():
    """无法映射的声明类型 → 防御性不干预。"""
    r = _rule("想换货")
    out = apply_declared_claim(r, "随便写")
    assert out is r


def test_return_claim_vs_exchange_text():
    """用户选「退货退款」但文本是换货 → 冲突转人工。"""
    r = _rule("换个新的给我")
    assert r.intent == IntentType.EXCHANGE
    out = apply_declared_claim(r, "退货退款")
    assert out.declared_conflict is True
    assert out.needs_human_review is True
