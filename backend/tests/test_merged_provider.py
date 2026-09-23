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


def test_merged_provider_samples_run_in_parallel():
    """P0 性能优化：sample_size 次采样并行发起，而非串行等待。

    验证方式：mock LLM 每次调用 sleep 0.3s。串行 3 次需 ≥0.9s；并行应显著更短。
    同时校验并行不改变聚合语义（中位数/usage 累加仍正确）。
    """
    import threading
    import time

    class SlowLLM(LLMClient):
        available = True

        def __init__(self):
            self.calls = 0
            self._lock = threading.Lock()
            self.last_usage = None

        def chat_json(self, system, user):
            with self._lock:
                self.calls += 1
            time.sleep(0.3)
            return {
                "fraud_score": 0.5, "fraud_features": ["x"],
                "sentiment_score": 0.4, "risk_level": "MEDIUM",
                "evidence_consistent": "consistent", "reason": "r",
            }

    llm = SlowLLM()
    p = MergedRiskProvider(llm=llm, sample_size=3)
    t0 = time.time()
    r = p.assess(description="商品破损", evidence_text="", refund_count=0)
    elapsed = time.time() - t0

    assert llm.calls == 3                    # 仍是 3 次采样（语义不变）
    assert r.fraud_score == 0.5              # 中位数聚合正确
    assert elapsed < 0.8, f"并行未生效：耗时 {elapsed:.2f}s（串行约 0.9s）"


def test_merged_provider_output_shapes_workflow():
    """验证合并输出字段与 workflow state 需要的字段一致。"""
    p = FakeMergedRiskProvider()
    r = p.assess(description="", evidence_text="", refund_count=0)
    # fraud_node 写入这些键（LangGraph state 共享给 sentiment_node/decision_node）
    keys = {"fraud_score", "fraud_features", "sentiment_score", "risk_level"}
    assert keys <= {k for k in r.__dataclass_fields__}


# ---- 工单 T202609101933114440C7 修复：凭证一致性惩罚的"描述含糊"兜底 ----
# 背景：描述"不想要了" + 凭证严重破损 -> LLM 判 inconsistent 触发 +30 分，
# 但"凭证比描述更严重"并非薅羊毛方向，属误伤。修复：含糊描述不叠加惩罚、降级 uncertain。

def test_vague_description_detector():
    """_description_is_vague：无质量关键词且过短 -> 含糊；含质量词 -> 明确。"""
    from app.agents.providers import _description_is_vague

    assert _description_is_vague("不想要了") is True       # 4 字、无质量词（修复根因）
    assert _description_is_vague("不想要") is True
    assert _description_is_vague("") is True
    assert _description_is_vague("商品碎了") is False      # 含"碎了"
    assert _description_is_vague("屏幕碎裂，申请退款") is False
    assert _description_is_vague("外壳破损") is False
    # 无质量词但写清了理由的长描述 -> 不算含糊（交由 LLM 语义判定）
    assert _description_is_vague("不喜欢这个颜色，想退货") is False


def test_vague_description_inconsistent_no_penalty_in_assess():
    """描述含糊（'不想要了'）+ LLM 判不一致：assess 保留 LLM 兜底信号但**不叠惩罚**，
    一致性判定/惩罚已独立到节点层（规则层对"无状态声明+图损坏"判 PARTIAL/UNCERTAIN，
    不再靠 assess 内降级误判）。"""
    llm = _SequenceLLM([{
        "fraud_score": 0.1, "fraud_features": [],
        "sentiment_score": 0.3, "risk_level": "MEDIUM",
        "evidence_consistent": "inconsistent",
        "reason": "客诉描述与凭证图片显示的严重损坏不符",
    }])
    r = MergedRiskProvider(llm=llm, sample_size=1).assess(
        description="不想要了", evidence_text="",
        vision_description="图片显示白色耳机外壳完全破碎", refund_count=0,
    )
    # LLM 原始信号保留（供节点层规则 UNCERTAIN 时兜底），但不降级、不叠加惩罚
    assert r.evidence_consistent == "inconsistent"
    assert r.evidence_penalty == 0.0               # 惩罚已移出 assess
    assert abs(r.fraud_score - 0.1) < 1e-9         # fraud_score 纯净
    assert "+30分" not in r.reason
