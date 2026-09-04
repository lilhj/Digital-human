"""模型 Provider 接口 + Fake 实现（Phase 5 工作流可测；Phase 6 替换真实实现）。

接口约定（Loop 提示词 Phase 6）：结构化输出、失败安全降级、Fake 可测。
"""
import logging
from dataclasses import dataclass

from app.agents.llm import LLMClient, LLMOutputError

logger = logging.getLogger("providers")


# ---------- OCR ----------

@dataclass
class OcrResult:
    text: str
    confidence: float | None
    status: str  # OK / TIMEOUT / LOW_CONFIDENCE / EMPTY


class OcrProvider:
    """OCR 接口。Phase 6 接入本地 PaddleOCR（OcrProvider 保持同一签名）。"""

    def extract(self, image_path: str) -> OcrResult:
        raise NotImplementedError


class FakeOcrProvider(OcrProvider):
    """测试用：无图返回 EMPTY；有图返回固定高置信文本（Phase 6 前）。"""

    def extract(self, image_path: str) -> OcrResult:
        if not image_path:
            return OcrResult(text="", confidence=None, status="EMPTY")
        return OcrResult(text="破损商品 退款 发票", confidence=0.95, status="OK")


# ---------- 欺诈风险 ----------

@dataclass
class FraudResult:
    score: float  # 0~1
    features: list[str]
    source: str  # llm / rule / fake


class FraudProvider:
    """欺诈风险评定：优先 LLM 语义分析，失败降级规则判定（高频退款等）。"""

    def __init__(self, llm: LLMClient | None = None, max_refund_count: int = 3):
        self.llm = llm or LLMClient()
        self.max_refund_count = max_refund_count

    def assess(self, *, description: str, evidence_text: str, refund_count: int) -> FraudResult:
        # 规则硬信号：高频退款直接高危（不依赖 LLM）
        if refund_count >= self.max_refund_count:
            return FraudResult(score=0.9, features=["高频退款"], source="rule")

        # 尽力 LLM 分析，失败降级为规则默认
        if self.llm.available and description.strip():
            try:
                data = self.llm.chat_json(
                    system="你是电商欺诈风控专家。分析客诉描述与凭证文本，判断是否涉嫌恶意退款/薅羊毛。"
                           "输出 JSON: {\"score\": 0~1 小数, \"features\": [字符串数组]}",
                    user=f"客诉描述：{description[:500]}\n凭证OCR文本：{evidence_text[:500]}",
                )
                score = max(0.0, min(1.0, float(data.get("score", 0.5))))
                features = [str(f) for f in data.get("features", [])][:5]
                return FraudResult(score=score, features=features, source="llm")
            except (LLMOutputError, ValueError, TypeError) as e:
                logger.warning("Fraud LLM 失败，降级规则: %s", e)
                # 规则降级：默认中风险（裁决 D-004：不因模型失败而自动放行）
                return FraudResult(score=0.3, features=["模型不可用降级"], source="rule")
        return FraudResult(score=0.1, features=[], source="rule")


class FakeFraudProvider(FraudProvider):
    """测试用：固定低风险。"""

    def assess(self, **kwargs) -> FraudResult:
        return FraudResult(score=0.1, features=[], source="fake")


# ---------- 舆情/情绪 ----------

@dataclass
class SentimentResult:
    score: float  # 0~1（风险向）
    level: str  # LOW / MEDIUM / HIGH


class SentimentProvider:
    """舆情情绪分析：LLM 语义分析客诉文本，失败默认中风险转人工复核（不误放行）。"""

    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or LLMClient()

    def assess(self, *, description: str, evidence_text: str) -> SentimentResult:
        if self.llm.available and description.strip():
            try:
                data = self.llm.chat_json(
                    system="你是客服舆情分析师。分析客诉文本的情绪强度与舆情升级风险。"
                           "输出 JSON: {\"score\": 0~1 小数（越高舆情风险越大）, \"level\": \"LOW\"|\"MEDIUM\"|\"HIGH\"}",
                    user=f"客诉描述：{description[:500]}\n凭证OCR文本：{evidence_text[:500]}",
                )
                score = max(0.0, min(1.0, float(data.get("score", 0.3))))
                level = str(data.get("level", "MEDIUM")).upper()
                if level not in ("LOW", "MEDIUM", "HIGH"):
                    level = "MEDIUM"
                return SentimentResult(score=score, level=level)
            except (LLMOutputError, ValueError, TypeError) as e:
                logger.warning("Sentiment LLM 失败，默认中风险: %s", e)
                return SentimentResult(score=0.3, level="MEDIUM")
        return SentimentResult(score=0.1, level="LOW")


class FakeSentimentProvider(SentimentProvider):
    """测试用：固定低风险。"""

    def assess(self, **kwargs) -> SentimentResult:
        return SentimentResult(score=0.1, level="LOW")


# ---------- 合并风控+舆情（工单5 成本优化移植：两次 LLM 调用合并为一次） ----------

@dataclass
class MergedRiskResult:
    fraud_score: float        # 0~1
    fraud_features: list[str]
    sentiment_score: float    # 0~1（风险向）
    risk_level: str           # LOW / MEDIUM / HIGH
    reason: str               # LLM 给出的一句话理由（可追溯打分依据）
    source: str               # llm / rule / fake
    usage: dict | None = None # telemetry：本次 LLM 调用的 token 用量（多采样累加；规则路径 None）


class MergedRiskProvider:
    """一次 LLM 调用同时输出风控分 + 舆情分（Token ↓约 40~50%，时延砍半）。

    优化前（工单1 现状）：Fraud 与 Sentiment 是两次独立 LLM 调用，且传入内容高度重复
    （都传 description + evidence_text）。
    优化后（工单5 合并）：单次输出 {fraud_score, fraud_features, sentiment_score, risk_level}。
    失败仍安全降级：默认中风险转人工，绝不因模型失败自动放行（裁决 D-004）。
    """

    def __init__(self, llm: LLMClient | None = None, max_refund_count: int = 3, sample_size: int = 3):
        self.llm = llm or LLMClient()
        self.max_refund_count = max_refund_count
        self.sample_size = sample_size  # 多次采样取中位数，磨掉单次 LLM 打分波动

    def _sample_once(self, description: str, evidence_text: str) -> dict:
        """单次 LLM 调用，返回消化后的 {fraud_score, fraud_features, sentiment_score, reason}。

        抛 LLMOutputError 由上层捕获降级。
        """
        data = self.llm.chat_json(
            # 锚定规则：要求风险等级与分数对账（HIGH 对应分数>0.5，MEDIUM 对应 0.2~0.5），
            # 并强制给一句话理由，便于追溯打分依据（修复 risk_level 与 sentiment 自相矛盾）
            system="你是电商风控与舆情双领域专家，一次输出两项评分。"
                   "评分必须与等级对账（锚定）：score>0.5 对应 HIGH，0.2<=score<=0.5 对应 MEDIUM，"
                   "<0.2 对应 LOW，两者不得矛盾。必须给一句话打分理由。"
                   "输出 JSON: {\"fraud_score\": 0~1, \"fraud_features\": [字符串],"
                   " \"sentiment_score\": 0~1, \"risk_level\": \"LOW\"|\"MEDIUM\"|\"HIGH\","
                   " \"reason\": \"一句话打分理由\"}",
            user=f"客诉描述：{description[:500]}\n凭证OCR文本：{evidence_text[:500]}",
        )
        return {
            "fraud_score": max(0.0, min(1.0, float(data.get("fraud_score", 0.5)))),
            "fraud_features": [str(f) for f in data.get("fraud_features", [])][:5],
            "sentiment_score": max(0.0, min(1.0, float(data.get("sentiment_score", 0.3)))),
            "reason": str(data.get("reason", "")).strip()[:200],
        }

    def assess(self, *, description: str, evidence_text: str, refund_count: int) -> MergedRiskResult:
        # 规则硬信号：高频退款直接高危（不依赖 LLM）
        if refund_count >= self.max_refund_count:
            return MergedRiskResult(
                fraud_score=0.9, fraud_features=["高频退款"],
                sentiment_score=0.3, risk_level="MEDIUM", source="rule",
                reason="高频退款规则命中（不依赖模型）",
            )

        if self.llm.available and description.strip():
            samples: list[dict] = []
            total_usage = {"prompt_tokens": 0, "completion_tokens": 0}
            try:
                # 多次采样：磨掉单次 LLM 打分波动（同输入可能 0.3~0.7 抖动）
                for _ in range(max(1, self.sample_size)):
                    samples.append(self._sample_once(description, evidence_text))
                    # telemetry：累加每次真实 LLM 调用的 token（规则/降级路径为 0）
                    # getattr 兜底兼容无 last_usage 的 mock/旧实现
                    u = getattr(self.llm, "last_usage", None)
                    if u:
                        total_usage["prompt_tokens"] += u.get("prompt_tokens", 0)
                        total_usage["completion_tokens"] += u.get("completion_tokens", 0)
            except (LLMOutputError, ValueError, TypeError) as e:
                logger.warning("合并风险 LLM 失败，降级规则: %s", e)
                # 规则降级：默认中风险（裁决 D-004：不因模型失败而自动放行）
                return MergedRiskResult(
                    fraud_score=0.3, fraud_features=["模型不可用降级"],
                    sentiment_score=0.3, risk_level="MEDIUM", source="rule",
                    reason=f"模型不可用降级: {e}",
                )

            # 取中位数（升序后中间值），抗离群
            def _median(vals: list[float]) -> float:
                sorted_v = sorted(vals)
                n = len(sorted_v)
                return (sorted_v[n // 2] if n % 2 else (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2)

            fraud_score = _median([s["fraud_score"] for s in samples])
            sentiment_score = _median([s["sentiment_score"] for s in samples])
            # 特征取并集去重（多轮更全面），限 5 个
            fraud_features = []
            for s in samples:
                for f in s["fraud_features"]:
                    if f not in fraud_features:
                        fraud_features.append(f)
            fraud_features = fraud_features[:5]
            # reason 取中位次样本的（最接近综合风险的采样）
            mid_idx = sorted(range(len(samples)), key=lambda i: samples[i]["sentiment_score"])[len(samples) // 2]
            reason = samples[mid_idx]["reason"] or (
                f"欺诈 {fraud_score:.2f} / 舆情 {sentiment_score:.2f}"
            )

            # 强一致：risk_level 依据综合风险分推导，不直接信任模型预测（避免矛盾）
            risk = max(fraud_score, sentiment_score)
            if risk > 0.5:
                level = "HIGH"
            elif risk >= 0.2:
                level = "MEDIUM"
            else:
                level = "LOW"

            return MergedRiskResult(
                fraud_score=fraud_score,
                fraud_features=fraud_features,
                sentiment_score=sentiment_score,
                risk_level=level,
                reason=reason,
                source="llm",
                usage=total_usage,
            )
        return MergedRiskResult(
            fraud_score=0.1, fraud_features=[], sentiment_score=0.1,
            risk_level="LOW", source="rule",
            reason="无客诉描述，默认低风险",
        )


class FakeMergedRiskProvider(MergedRiskProvider):
    """测试用：固定低风险（等价 FakeFraudProvider + FakeSentimentProvider）。"""

    def assess(self, **kwargs) -> MergedRiskResult:
        return MergedRiskResult(
            fraud_score=0.1, fraud_features=[], sentiment_score=0.1,
            risk_level="LOW", reason="fake（测试固定低风险）", source="fake",
        )
