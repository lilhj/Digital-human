"""工单8 双层意图识别（退货/换货/退款）。

双层漏斗（先便宜后昂贵，对齐 PRD v4.0 §4.1）：
- Node A 规则（RuleBasedIntentProvider）：明确关键词直接判，零 LLM 调用、零延迟。
- Node B 大模型（LlmIntentProvider）：口语化/模糊表述才精判，严格 JSON 输出。
- 兜底（Fallback）：规则未命中且 LLM 损坏/超时/未配置 → 返回 UNKNOWN，
  由意图节点路由到「转人工复核」，绝不因模型失败自动放行（裁决 D-004）。

Provider 注入：模块级默认 HybridIntentProvider（RuleBased + FakeLlm），零依赖可隔离测试；
use_fake_providers=False 时由 factory 注入真实 LlmIntentProvider(LLMClient)。
"""
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar

from app.agents.llm import LLMClient, LLMOutputError

logger = logging.getLogger("intent")

# 三意图枚举（PRD §5 锁定：退货/换货/退款）
REFUND = "REFUND"      # 退款（退钱）
RETURN = "RETURN"      # 退货（退回商品）
EXCHANGE = "EXCHANGE"  # 换货（换新/更换）
UNKNOWN = "UNKNOWN"    # 兜底：无法确定 → 转人工


class IntentType(str, Enum):
    REFUND = REFUND
    RETURN = RETURN
    EXCHANGE = EXCHANGE
    UNKNOWN = UNKNOWN

    def __str__(self) -> str:  # 便于 JSON/dict 直接存字符串值
        return self.value


# 规则引擎关键词树（PRD §4.1 例子 + 电商售后常见表述；退款优先于退货优先于换货）
_RULE_PATTERNS: ClassVar[list[tuple[re.Pattern, IntentType, float]]] = [
    # 退款类（退钱/返现/退费）
    (re.compile(r"退款|退钱|退费|返现|退回款|退回来钱|把钱退|原路退|退我钱|退我款|申请退|要求退", re.IGNORECASE),
     IntentType.REFUND, 0.95),
    # 退货类（退回商品）
    (re.compile(r"退货|退回商品|退回来|寄回|退掉这?个?货|把货退|商品退回|无理由退|七?天?无理由", re.IGNORECASE),
     IntentType.RETURN, 0.95),
    # 换货类（换新/更换）
    (re.compile(r"换货|换新|换一个|更换|换个|给我换|重新发|补发一个|换同款|换新的|发错.*换", re.IGNORECASE),
     IntentType.EXCHANGE, 0.95),
]

# 规则置信阈值：命中即采信（Node A 高置信直接判，无需 LLM）
RULE_THRESHOLD = 0.8


@dataclass
class IntentResult:
    intent: IntentType
    source: str          # "rule" | "llm" | "fallback" | "fake" | "declared"
    confidence: float
    reason: str
    usage: dict | None = None  # telemetry：LLM 层的 token 用量（规则命中/兜底为 None）
    declared_conflict: bool = False  # 工单8 交叉校验：用户显式选择与文本识别冲突

    @property
    def is_exchange(self) -> bool:
        return self.intent == IntentType.EXCHANGE

    @property
    def needs_human_review(self) -> bool:
        """兜底路由：换货、意图不明，或用户声明与文本识别冲突 → 转人工（PRD §4.1/§4.2）。"""
        return (
            self.declared_conflict
            or self.intent in (IntentType.EXCHANGE, IntentType.UNKNOWN)
        )


# 工单8 交叉校验：买家端显式三选一（前端卡片）→ 意图枚举映射。
# 用户在前端页面自行选择的售后类型，作为识别结果的主通道与交叉校验基准。
CLAIM_TO_INTENT: dict[str, IntentType] = {
    "退款": IntentType.REFUND,
    "退货退款": IntentType.RETURN,
    "换货": IntentType.EXCHANGE,
}


def apply_declared_claim(result: IntentResult, claim_type: str | None) -> IntentResult:
    """工单8 交叉校验：以用户显式选择为主通道，与双层识别结果做一致性判定。

    用户在前端三选一（退款/退货退款/换货）→ claim_type 随案件落库；识别管道仍跑
    双层规则+LLM（员工侧建单 / 旧案件无 claim_type，保持原行为）。

    判定逻辑（资损零容忍）：
    - 无声明类型 → 原样返回，双层识别保持主通道；
    - 声明与识别一致 → 采信（source=declared，置信度拉高）；
    - 识别 UNKNOWN（规则未命中且 LLM 兜底）→ 采信用户显式选择（主通道原则）；
    - 声明与识别冲突（如用户选「退款」但描述却说「想换货」）→ declared_conflict=True
      转人工复核——宁可挂起让人看一眼，也不自动放行错误意图。
    """
    if not claim_type:
        return result
    declared = CLAIM_TO_INTENT.get(claim_type)
    if declared is None:
        return result  # 无法映射的声明类型（防御），不干预
    if result.intent == declared:
        # 一致：用户显式选择确认了识别结果
        return IntentResult(
            intent=declared,
            source="declared",
            confidence=max(result.confidence, 0.95),
            reason=f"用户显式选择「{claim_type}」，与双层识别一致",
            usage=result.usage,
        )
    if result.intent == IntentType.UNKNOWN:
        # 文本未识别出明确意图，但用户有显式选择 → 以用户选择为主通道
        return IntentResult(
            intent=declared,
            source="declared",
            confidence=0.95,
            reason=f"文本未识别明确意图，采信用户显式选择「{claim_type}」",
            usage=result.usage,
        )
    # 冲突：用户选择与文本识别不一致 → 转人工复核
    return IntentResult(
        intent=declared,  # 保留用户主张（而非文本误判），由人工定夺
        source="declared",
        confidence=max(result.confidence, 0.6),
        reason=f"冲突：用户选择「{claim_type}」但文本识别为 {result.intent.value}，转人工复核",
        usage=result.usage,
        declared_conflict=True,
    )


class IntentProvider(ABC):
    @abstractmethod
    def classify(self, description: str, masked: str | None = None) -> IntentResult:
        """对退款诉求文本做意图识别；masked 为脱敏后文本（供 LLM 层合规使用）。"""


class RuleBasedIntentProvider(IntentProvider):
    """Node A：关键词规则，零 LLM、确定性。未命中返回 UNKNOWN（交 Node B）。"""

    def classify(self, description: str, masked: str | None = None) -> IntentResult:
        text = description or ""
        if not text.strip():
            return IntentResult(IntentType.UNKNOWN, "rule", 0.0, "空描述")
        best: IntentResult | None = None
        for pat, intent, weight in _RULE_PATTERNS:
            if pat.search(text):
                # 多命中按权重取最高（退款/退货/换货优先级已在列表顺序，权重相同取首个）
                if best is None or weight > best.confidence:
                    best = IntentResult(intent, "rule", weight, f"规则命中：{pat.pattern[:24]}")
        if best is None:
            return IntentResult(IntentType.UNKNOWN, "rule", 0.0, "规则未命中，需 LLM 精判")
        return best


class LlmIntentProvider(IntentProvider):
    """Node B：大模型意图判定，严格 JSON 输出。

    LLM 不可用 / 损坏 JSON / 超时 → 返回 UNKNOWN（由 Hybrid 转 fallback），绝不抛出导致链路崩溃。
    """

    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or LLMClient()

    _SYSTEM = (
        "你是电商售后意图分类专家。判断用户诉求属于哪一类，仅输出 JSON。"
        "类别枚举：REFUND=仅退款（退钱，不涉及退商品）；RETURN=退货（退回商品并退款）；"
        "EXCHANGE=换货（换一个新的，不涉及退款金额）。"
        "输出 JSON: {\"intent\": \"REFUND\"|\"RETURN\"|\"EXCHANGE\", \"confidence\": 0~1, \"reason\": \"一句话\"}"
    )

    def classify(self, description: str, masked: str | None = None) -> IntentResult:
        text = (masked if masked is not None else description) or ""
        if not self.llm.available or not text.strip():
            return IntentResult(IntentType.UNKNOWN, "llm", 0.0, "LLM 未配置/无文本")
        try:
            data = self.llm.chat_json(
                system=self._SYSTEM,
                user=f"用户诉求：{text[:500]}",
            )
        except (LLMOutputError, ValueError, TypeError) as e:
            logger.warning("意图 LLM 失败，转兜底: %s", e)
            return IntentResult(IntentType.UNKNOWN, "llm", 0.0, f"LLM 失败: {e}")
        raw = str(data.get("intent", "")).upper()
        try:
            intent = IntentType(raw)
        except ValueError:
            return IntentResult(IntentType.UNKNOWN, "llm", 0.0, f"LLM 返回未知意图: {raw}")
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.6))))
        return IntentResult(
            intent, "llm", confidence, str(data.get("reason", ""))[:200],
            usage=self.llm.last_usage,
        )


class FakeLlmIntentProvider(LlmIntentProvider):
    """测试/开发用：LLM 未配置时等价于降级（返回 UNKNOWN），不发起真实调用。"""

    def __init__(self):
        # 持有不可用 LLM（available=False），classify 直接降级
        self.llm = LLMClient(api_key="")

    def classify(self, description: str, masked: str | None = None) -> IntentResult:
        return IntentResult(IntentType.UNKNOWN, "llm", 0.0, "FakeLlm（降级，未配置 API）")


class FakeIntentProvider(IntentProvider):
    """测试用：固定/字典映射，便于断言双层与路由。"""

    def __init__(self, fixed: IntentType = IntentType.REFUND):
        self.fixed = fixed

    def classify(self, description: str, masked: str | None = None) -> IntentResult:
        return IntentResult(self.fixed, "fake", 0.9, "测试固定意图")


class HybridIntentProvider(IntentProvider):
    """双层漏斗编排（Node A 规则 + Node B LLM + Fallback 兜底）。

    classify(raw, masked)：用 raw 跑规则（保关键词完整），用 masked 跑 LLM（合规不喂 PII）。
    规则高置信 → 直接采信；否则 LLM；LLM 失败/未知 → UNKNOWN 兜底转人工。
    """

    def __init__(self, rule: IntentProvider | None = None, llm: IntentProvider | None = None):
        self.rule = rule or RuleBasedIntentProvider()
        self.llm = llm or FakeLlmIntentProvider()

    def classify(self, description: str, masked: str | None = None) -> IntentResult:
        # Node A：规则先判（零成本）
        rule_res = self.rule.classify(description)
        if rule_res.intent != IntentType.UNKNOWN and rule_res.confidence >= RULE_THRESHOLD:
            return rule_res
        # Node B：口语化/模糊表述才调 LLM
        try:
            llm_res = self.llm.classify(description, masked=masked)
        except Exception as e:  # noqa: BLE001 - 任何异常都走兜底，不崩链路
            logger.warning("意图双层 LLM 异常，转兜底: %s", e)
            return IntentResult(IntentType.UNKNOWN, "fallback", 0.0, f"LLM 异常: {e}")
        if llm_res.intent != IntentType.UNKNOWN:
            return llm_res
        # Fallback 保守兜底（裁决 D-004：不因模型失败自动放行）
        return IntentResult(
            IntentType.UNKNOWN, "fallback", 0.0,
            "规则与 LLM 均未判定，保守兜底转人工复核",
        )


# 模块级默认：规则引擎常驻；LLM 默认 Fake（零依赖隔离测试 / 无 API key 降级）
intent_provider: IntentProvider = HybridIntentProvider()
