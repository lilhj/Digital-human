"""工单8 双层意图识别单元测试（无 Redis/Postgres 依赖，纯单测）。

覆盖：规则层、LLM 层（含坏 JSON 兜底）、混合流短路与兜底、IntentResult 属性、
graph 路由、死信队列降级、周期评测指标（Recall/TTFT/Token 降幅）。

命名用例（计划要求）：
- test_json_parse_fallback：LLM 吐坏 JSON → 兜底转人工
- test_eval_runner_metric：评测结果含 Recall + TTFT
"""
import pytest

from app.agents.llm import LLMClient, LLMOutputError
from app.eval import intent_benchmark as ib
from app.intent import (
    EXCHANGE,
    REFUND,
    RETURN,
    UNKNOWN,
    FakeIntentProvider,
    FakeLlmIntentProvider,
    HybridIntentProvider,
    IntentResult,
    IntentType,
    LlmIntentProvider,
    RULE_THRESHOLD,
    RuleBasedIntentProvider,
)
from app.workflow.graph import _route_from_intent, build_graph


# ---------- 本地 stub LLM（不真实联网）----------

class BrokenJsonLLM(LLMClient):
    """模拟模型返回非法 JSON / 解析失败 -> 抛 LLMOutputError。"""

    def __init__(self):
        super().__init__(api_key="stub")

    @property
    def available(self) -> bool:
        return True

    def chat_json(self, system: str, user: str) -> dict:
        raise LLMOutputError("模拟模型返回非法 JSON")


class SimpleJsonLLM(LLMClient):
    """返回固定合法 JSON 的 stub。"""

    def __init__(self, intent: str = RETURN, conf: float = 0.9):
        super().__init__(api_key="stub")
        self._intent = intent
        self._conf = conf

    @property
    def available(self) -> bool:
        return True

    def chat_json(self, system: str, user: str) -> dict:
        return {"intent": self._intent, "confidence": self._conf, "reason": "stub"}


class RecordingLlm(LLMClient):
    """记录 chat_json 收到的 user 文本，验证 LLM 层拿到的是脱敏文本。"""

    def __init__(self):
        super().__init__(api_key="stub")
        self.calls: list[str] = []

    @property
    def available(self) -> bool:
        return True

    def chat_json(self, system: str, user: str) -> dict:
        self.calls.append(user)
        return {"intent": REFUND, "confidence": 0.9, "reason": "stub"}


# ---------- 规则层 ----------

class TestRuleBasedIntentProvider:
    def test_refund_keywords(self):
        p = RuleBasedIntentProvider()
        for text in ["我要退款", "把钱退回来", "申请退费", "原路退回款"]:
            assert p.classify(text).intent == IntentType.REFUND

    def test_return_keywords(self):
        p = RuleBasedIntentProvider()
        for text in ["我要退货", "把货寄回", "七天无理由退", "商品退回"]:
            assert p.classify(text).intent == IntentType.RETURN

    def test_exchange_keywords(self):
        p = RuleBasedIntentProvider()
        for text in ["换货", "给我换一个新的", "重新发一个", "发错了换一个"]:
            assert p.classify(text).intent == IntentType.EXCHANGE

    def test_empty_returns_unknown(self):
        assert RuleBasedIntentProvider().classify("").intent == IntentType.UNKNOWN

    def test_unrelated_text_unknown(self):
        assert RuleBasedIntentProvider().classify("今天天气不错").intent == IntentType.UNKNOWN

    def test_confidence_is_point_nine_five(self):
        r = RuleBasedIntentProvider().classify("我要退款")
        assert r.confidence == 0.95
        assert r.source == "rule"

    def test_threshold_constant(self):
        assert RULE_THRESHOLD == 0.8

    def test_priority_refund_over_return(self):
        # 同时含退款/退货关键词时，列表首位 REFUND 优先
        r = RuleBasedIntentProvider().classify("我要退货并且退款")
        assert r.intent == IntentType.REFUND

    def test_rule_above_threshold_short_circuits(self):
        r = RuleBasedIntentProvider().classify("申请退款")
        assert r.confidence >= RULE_THRESHOLD


# ---------- IntentResult 属性 ----------

class TestIntentResult:
    def test_is_exchange_true(self):
        assert IntentResult(IntentType.EXCHANGE, "rule", 0.9, "").is_exchange is True

    def test_is_exchange_false_for_refund(self):
        assert IntentResult(IntentType.REFUND, "rule", 0.9, "").is_exchange is False

    def test_needs_human_review_exchange(self):
        assert IntentResult(IntentType.EXCHANGE, "rule", 0.9, "").needs_human_review is True

    def test_needs_human_review_unknown(self):
        assert IntentResult(IntentType.UNKNOWN, "fallback", 0.0, "").needs_human_review is True

    def test_needs_human_review_refund_false(self):
        assert IntentResult(IntentType.REFUND, "rule", 0.95, "").needs_human_review is False

    def test_needs_human_review_return_false(self):
        assert IntentResult(IntentType.RETURN, "rule", 0.95, "").needs_human_review is False


# ---------- LLM 层（含坏 JSON 兜底）----------

class TestLlmIntentProvider:
    def test_unavailable_returns_unknown(self):
        # FakeLlmIntentProvider 持有不可用 LLM -> 降级 UNKNOWN
        from app.intent import FakeLlmIntentProvider

        r = FakeLlmIntentProvider().classify("我要换货")
        assert r.intent == IntentType.UNKNOWN

    def test_json_parse_fallback(self):
        """LLM 吐坏 JSON（解析失败）-> 兜底 UNKNOWN -> 转人工复核（裁决 D-004）。"""
        llm = LlmIntentProvider(BrokenJsonLLM())
        # 用无规则命中的模糊文本，确保走 LLM 路径
        r = llm.classify("这个东西我不想要了")
        assert r.intent == IntentType.UNKNOWN
        assert r.needs_human_review is True

    def test_valid_json_parsed(self):
        llm = LlmIntentProvider(SimpleJsonLLM(intent=RETURN, conf=0.9))
        r = llm.classify("我要退货")
        assert r.intent == IntentType.RETURN
        assert r.source == "llm"


# ---------- 混合流（短路 + 兜底）----------

class TestHybridIntentProvider:
    def test_rule_short_circuits_llm(self):
        rec = RecordingLlm()
        hyb = HybridIntentProvider(rule=RuleBasedIntentProvider(), llm=LlmIntentProvider(rec))
        r = hyb.classify("我要退款", masked="我要退款")
        assert r.intent == IntentType.REFUND
        assert r.source == "rule"
        assert rec.calls == []  # 规则命中，LLM 未被调用（省 Token）

    def test_rule_miss_then_llm(self):
        hyb = HybridIntentProvider(
            rule=RuleBasedIntentProvider(),
            llm=LlmIntentProvider(SimpleJsonLLM(intent=RETURN, conf=0.9)),
        )
        r = hyb.classify("这个东西我不想要了", masked="这个东西我不想要了")
        assert r.intent == IntentType.RETURN
        assert r.source == "llm"

    def test_hybrid_fallback_when_llm_also_unknown(self):
        # 规则未命中 + LLM 降级 -> 保守兜底 UNKNOWN -> 转人工
        hyb = HybridIntentProvider(
            rule=RuleBasedIntentProvider(), llm=FakeLlmIntentProvider()
        )
        r = hyb.classify("这个东西我不想要了", masked="这个东西我不想要了")
        assert r.intent == IntentType.UNKNOWN
        assert r.source == "fallback"
        assert r.needs_human_review is True

    def test_masked_passed_to_llm(self):
        rec = RecordingLlm()
        hyb = HybridIntentProvider(rule=RuleBasedIntentProvider(), llm=LlmIntentProvider(rec))
        # 无意图关键词但含 PII：规则层不命中 -> 调 LLM，且 LLM 应拿到脱敏文本
        hyb.classify("我的手机号13800138000东西坏了", masked="我的手机号1****8000东西坏了")
        assert rec.calls, "规则未命中时应调用 LLM"
        assert "1****8000" in rec.calls[0]  # LLM 拿到脱敏文本

    def test_exchange_routes_human(self):
        hyb = HybridIntentProvider(rule=RuleBasedIntentProvider(), llm=FakeIntentProvider(IntentType.EXCHANGE))
        r = hyb.classify("这个想换货", masked="这个想换货")
        assert r.intent == IntentType.EXCHANGE
        assert r.needs_human_review is True

    def test_fake_intent_provider_fixed(self):
        assert FakeIntentProvider(IntentType.RETURN).classify("x").intent == IntentType.RETURN

    def test_enum_str_values(self):
        assert str(IntentType.REFUND) == REFUND == "REFUND"
        assert str(IntentType.EXCHANGE) == EXCHANGE == "EXCHANGE"


# ---------- graph 路由 ----------

class TestIntentRouting:
    def test_route_exchange_to_human_review(self):
        assert _route_from_intent({"intent_fallback": True, "intent": EXCHANGE}) == "human_review"

    def test_route_unknown_to_human_review(self):
        assert _route_from_intent({"intent_fallback": True, "intent": UNKNOWN}) == "human_review"

    def test_route_refund_to_order_verify(self):
        assert _route_from_intent({"intent_fallback": False, "intent": REFUND}) == "order_verify"

    def test_route_return_to_order_verify(self):
        assert _route_from_intent({"intent_fallback": False, "intent": RETURN}) == "order_verify"

    def test_route_default_no_fallback_field(self):
        # 缺省无 intent_fallback 视为非兜底 -> 走常规订单三查
        assert _route_from_intent({}) == "order_verify"

    def test_graph_contains_intent_node(self):
        g = build_graph()
        assert "intent" in g.nodes
        assert "intake" in g.nodes


# ---------- 死信队列降级 ----------

class TestDeadLetterSafety:
    def test_enqueue_no_raise_when_redis_down(self, monkeypatch):
        import app.infrastructure.dead_letter as dlq

        def boom(*a, **k):
            raise RuntimeError("redis down")

        monkeypatch.setattr(dlq, "get_redis", boom)
        # 绝不抛出，仅降级告警
        dlq.enqueue("intent", 1, {"x": 1}, "boom")

    def test_length_safe_when_redis_down(self, monkeypatch):
        import app.infrastructure.dead_letter as dlq

        def boom(*a, **k):
            raise RuntimeError("redis down")

        monkeypatch.setattr(dlq, "get_redis", boom)
        assert dlq.length() == 0


# ---------- 周期评测指标 ----------

class TestEvalRunner:
    def test_eval_runner_metric(self):
        """评测结果须含 Recall 与 TTFT（工单8 验收指标）。"""
        res = ib.run_intent_benchmark()
        assert "Recall" in res
        assert "TTFT" in res
        assert res["Recall"] >= 0.90
        assert res["TokenReduction"] >= 0.40

    def test_benchmark_sample_count_ge_100(self):
        samples = ib.generate_samples()
        assert len(samples) >= 100

    def test_token_reduction_meets_red_line(self):
        res = ib.run_intent_benchmark()
        assert res["TokenReduction"] >= 0.40
        assert res["HallucinationRate"] <= 0.02
