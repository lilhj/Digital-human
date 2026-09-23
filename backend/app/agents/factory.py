"""Provider 工厂：按环境配置选择真实实现或 Fake 实现。

- OCR：paddle 可导入 -> PaddleOcrProvider（本地模型）；否则 Fake + 警告
- 风控+舆情（工单5 合并）：LLM_API_KEY 已配置 -> MergedRiskProvider（一次调用双输出）；
  否则 Fake（开发/测试模式）
"""
import logging

from app.agents.providers import (
    FakeMergedRiskProvider,
    FakeOcrProvider,
    MergedRiskProvider,
)
from app.core.config import get_settings
from app.workflow.order_verify import DbOrderVerifyProvider, FakeOrderVerifyProvider

# 工单6 安全网关（Critic/DLP/Tool 过滤）：规则引擎零依赖，默认真实常驻
from app.security.critic import CriticProvider, NoopCriticProvider, RuleBasedCriticProvider
from app.security.dlp import DLPProvider, NoopDlpProvider, RuleBasedDlpProvider

# 工单8 双层意图识别：规则引擎常驻 + 可选真实 LLM
from app.intent import HybridIntentProvider, LlmIntentProvider

logger = logging.getLogger("factory")


def create_ocr_provider():
    try:
        import paddle  # noqa: F401

        from app.agents.paddle_ocr import PaddleOcrProvider

        provider = PaddleOcrProvider()
        logger.info("OCR Provider: PaddleOCR 本地推理")
        try:
            provider.warmup()  # 预热算子，避免首个工单超时
        except Exception:  # noqa: BLE001
            pass
        return provider
    except ImportError:
        logger.warning("paddle 未安装，OCR 使用 FakeProvider（Phase 6 需安装 paddlepaddle+paddleocr）")
        return FakeOcrProvider()


def create_risk_providers():
    """返回 MergedRiskProvider（工单5：Fraud+Sentiment 合并为一次 LLM 调用）。"""
    settings = get_settings()
    if settings.use_fake_providers:
        logger.warning("USE_FAKE_PROVIDERS=true：风险分析使用 FakeProvider（低风险测试模式，场景二联调用）")
        return FakeMergedRiskProvider()
    if settings.llm_api_key:
        from app.agents.llm import LLMClient

        logger.info("风险 Provider: 合并 LLM（%s）", settings.llm_model)
        return MergedRiskProvider(llm=LLMClient())
    logger.warning("LLM_API_KEY 未配置，风险分析使用 FakeProvider（开发模式）")
    return FakeMergedRiskProvider()


def create_vision_provider():
    """视觉理解 Provider：Qwen2.5-VL via 本地 Ollama（凭证图片语义描述）。

    降级策略（延续"降级不阻断"哲学，绝不造假）：
    - 测试(TESTING=1) / use_fake_providers / vision_enabled=false / Ollama 不可达
      -> NoopVisionProvider（跳过一致性校验，不加分不阻断，不编造图片描述）。
    """
    import os

    from app.agents.vision import NoopVisionProvider, OllamaVisionProvider

    settings = get_settings()
    if os.environ.get("TESTING") == "1" or settings.use_fake_providers:
        logger.warning("视觉理解使用 NoopProvider（测试/隔离，跳过凭证一致性校验）")
        return NoopVisionProvider()
    if not settings.vision_enabled:
        logger.warning("VISION_ENABLED=false：视觉理解已关闭（跳过 VL）")
        return NoopVisionProvider()
    provider = OllamaVisionProvider()
    if not provider.available:
        logger.warning("Ollama 不可达（%s）：视觉理解降级 Noop（一致性校验跳过）",
                       settings.ollama_base_url)
        return NoopVisionProvider()
    logger.info("视觉 Provider: OllamaVisionProvider(%s)", provider.model)
    return provider


def create_order_verify_provider():
    """订单三查 Provider：默认真实查库（资损红线）；use_fake_providers 时放行（测试/调试）。"""
    settings = get_settings()
    if settings.use_fake_providers:
        logger.warning("USE_FAKE_PROVIDERS=true：订单三查使用 FakeProvider（放行，不校验真实订单）")
        return FakeOrderVerifyProvider()
    logger.info("订单三查 Provider: DbOrderVerifyProvider（直查 Order/OrderItem）")
    return DbOrderVerifyProvider()


def create_critic_provider() -> CriticProvider:
    """Critic 语义安检 Provider：默认规则引擎常驻（零依赖、主动防御）；
    use_fake_providers 时降级 Noop（隔离测试不拦截）。"""
    settings = get_settings()
    if settings.use_fake_providers:
        logger.warning("USE_FAKE_PROVIDERS=true：Critic 使用 NoopProvider（不拦截，隔离测试）")
        return NoopCriticProvider()
    logger.info("Critic Provider: RuleBasedCriticProvider（规则+启发式双引擎）")
    return RuleBasedCriticProvider()


def create_dlp_provider() -> DLPProvider:
    """DLP 脱敏 Provider：默认规则引擎常驻；use_fake_providers 时降级 Noop（不脱敏）。"""
    settings = get_settings()
    if settings.use_fake_providers:
        logger.warning("USE_FAKE_PROVIDERS=true：DLP 使用 NoopProvider（不脱敏，隔离测试）")
        return NoopDlpProvider()
    logger.info("DLP Provider: RuleBasedDlpProvider（正则脱敏）")
    return RuleBasedDlpProvider()


def create_intent_provider() -> HybridIntentProvider:
    """工单8 双层意图识别 Provider：规则引擎常驻（零依赖、主动防御兜底）；
    use_fake_providers 时 LLM 层降级 FakeLlm（隔离测试不调真实模型）；
    LLM_API_KEY 已配置时注入真实 LlmIntentProvider（规则 + LLM 混合流）。
    """
    settings = get_settings()
    if settings.use_fake_providers:
        logger.warning("USE_FAKE_PROVIDERS=true：意图 LLM 层使用 FakeLlm（规则常驻，隔离测试）")
        return HybridIntentProvider()
    if settings.llm_api_key:
        from app.agents.llm import LLMClient

        logger.info("意图 Provider: Hybrid（规则 + LlmIntentProvider(%s)）", settings.llm_model)
        return HybridIntentProvider(llm=LlmIntentProvider(LLMClient()))
    logger.warning("LLM_API_KEY 未配置，意图识别 Hybrid 使用 FakeLlm（降级，不自动放行）")
    return HybridIntentProvider()


def configure_providers() -> None:
    """初始化全局 Provider（Worker/API 启动时调用一次）。"""
    from app.workflow import nodes

    nodes.ocr_provider = create_ocr_provider()
    nodes.vision_provider = create_vision_provider()
    nodes.merged_risk_provider = create_risk_providers()
    nodes.order_verify_provider = create_order_verify_provider()
    nodes.critic_provider = create_critic_provider()
    nodes.dlp_provider = create_dlp_provider()
    nodes.intent_provider = create_intent_provider()
