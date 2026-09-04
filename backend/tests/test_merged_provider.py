"""合并风控+舆情 Provider 测试（工单5 成本优化：两次 LLM 调用合并为一次 + 三次采样取中位数）。

核心断言：
1. 三次采样同出 fraud_score + sentiment_score + risk_level，取中位数抗单次打分波动；
2. 失败安全降级（不因模型失败自动放行）；
3. Fake 模式等价低风险（场景二联调用）。
"""
import pytest

from app.agents.llm import LLMClient, LLMOutputError
from app.agents.providers import FakeMergedRiskProvider, MergedRiskProvider


class _SequenceLLM(LLMClient):
    """按调用次数依次返回预设结果，用于验证中位数聚合。"""

    available = True
    calls = 0
    responses: list[dict]

    def __init__(self, responses: list[dict]):
        self.responses = responses

    def chat_json(self, system, user):
        self.calls += 1
        return self.responses[self.calls - 1]


class _RecordingLLM(_SequenceLLM):
    """固定响应，记录调用次数（3 次采样全同）。"""

    def __init__(self):
        super().__init__([
            {"fraud_score": 0.75, "fraud_features": ["描述矛盾", "高频"],
             "sentiment_score": 0.6, "risk_level": "HIGH"} for _ in range(3)
        ])


def test_merged_provider_three_samples_median():
    """三次采样取中位数（磨掉单次打分波动），分数聚合、特征并集。"""
    llm = _SequenceLLM([
        {"fraud_score": 0.1, "fraud_features": ["破损"], "sentiment_score": 0.2,
         "risk_level": "LOW", "reason": "低分"},
        {"fraud_score": 0.9, "fraud_features": ["高频"], "sentiment_score": 0.8,
         "risk_level": "HIGH", "reason": "高分"},
        {"fraud_score": 0.5, "fraud_features": ["破损", "高频"], "sentiment_score": 0.5,
         "risk_level": "MEDIUM", "reason": "中分"},
    ])
    p = MergedRiskProvider(llm=llm)
    r = p.assess(description="收到破损商品", evidence_text="发票模糊", refund_count=0)
    assert llm.calls == 3  # 关键：固定 3 次采样
    assert r.fraud_score == 0.5         # median(0.1, 0.9, 0.5)
    assert r.sentiment_score == 0.5     # median(0.2, 0.8, 0.5)
    assert r.risk_level == "MEDIUM"     # max(0.5, 0.5) >= 0.2 -> MEDIUM
    assert r.source == "llm"
    # 特征并集去重后包含全部
    assert "破损" in r.fraud_features and "高频" in r.fraud_features


def test_merged_provider_stable_values():
    """响应全同时中位数=该值；调用次数=3。"""
    llm = _RecordingLLM()
    p = MergedRiskProvider(llm=llm)
    r = p.assess(description="收到破损商品", evidence_text="发票模糊", refund_count=0)
    assert llm.calls == 3
    assert r.fraud_score == 0.75
    assert r.sentiment_score == 0.6
    assert r.risk_level == "HIGH"   # max(0.75, 0.6) > 0.5
    assert r.source == "llm"
    assert "描述矛盾" in r.fraud_features


def test_merged_provider_high_refund_rule():
    """高频退款规则硬信号：不依赖 LLM 直接高危（原始 FraudProvider 行为保留）。"""
    llm = _RecordingLLM()
    p = MergedRiskProvider(llm=llm, max_refund_count=3)
    r = p.assess(description="x", evidence_text="", refund_count=5)
    assert llm.calls == 0  # 规则直出，零调用
    assert r.fraud_score == 0.9
    assert r.source == "rule"


def test_merged_provider_llm_failure_fallback():
    """LLM 失败 -> 默认中风险降级（裁决 D-004：不因模型失败自动放行）。"""

    class BoomLLM(LLMClient):
        available = True

        def chat_json(self, system, user):
            raise LLMOutputError("模型超时")

    p = MergedRiskProvider(llm=BoomLLM())
    r = p.assess(description="x", evidence_text="", refund_count=0)
    assert r.source == "rule"
    assert r.fraud_score == 0.3
    assert r.sentiment_score == 0.3
    assert r.risk_level == "MEDIUM"


def test_fake_merged_provider_low_risk():
    """Fake 模式：低风险（场景二联调用，等价原 FakeFraud+FakeSentiment）。"""
    r = FakeMergedRiskProvider().assess(description="x", evidence_text="", refund_count=0)
    assert r.fraud_score == 0.1
    assert r.sentiment_score == 0.1
    assert r.risk_level == "LOW"
    assert r.source == "fake"


def test_merged_provider_output_shapes_workflow():
    """验证合并输出字段与 workflow state 需要的字段一致。"""
    p = FakeMergedRiskProvider()
    r = p.assess(description="", evidence_text="", refund_count=0)
    # fraud_node 写入这些键（LangGraph state 共享给 sentiment_node/decision_node）
    keys = {"fraud_score", "fraud_features", "sentiment_score", "risk_level"}
    assert keys <= {k for k in r.__dataclass_fields__}
