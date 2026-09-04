"""工单8 双层意图识别模块（退货/换货/退款）。

导出核心类与模块级默认 Provider。节点编排见 app.workflow.nodes.intent_node。
"""
from app.intent.classifier import (
    CLAIM_TO_INTENT,
    EXCHANGE,
    REFUND,
    RETURN,
    UNKNOWN,
    FakeIntentProvider,
    FakeLlmIntentProvider,
    HybridIntentProvider,
    IntentProvider,
    IntentResult,
    IntentType,
    LlmIntentProvider,
    RULE_THRESHOLD,
    RuleBasedIntentProvider,
    apply_declared_claim,
    intent_provider,
)

__all__ = [
    "IntentType",
    "IntentResult",
    "IntentProvider",
    "RuleBasedIntentProvider",
    "LlmIntentProvider",
    "FakeLlmIntentProvider",
    "FakeIntentProvider",
    "HybridIntentProvider",
    "intent_provider",
    "REFUND",
    "RETURN",
    "EXCHANGE",
    "UNKNOWN",
    "RULE_THRESHOLD",
]
