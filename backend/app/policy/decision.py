"""决策策略（DecisionPolicy）：确定性规则路由，金额/阈值不由大模型决定。

裁决落地：
- D-001 金额 > 300 元（amount_limit_cent）-> HUMAN_REVIEW
- D-002 OCR 置信度 < 0.5 -> HUMAN_REVIEW；0.5~0.7 -> HUMAN_REVIEW（预警）
- D-003 风险分 > 0.5 -> REJECT；0.2~0.5 -> HUMAN_REVIEW；<= 0.2 -> APPROVE
- D-004 模型异常（errors 非空）-> HUMAN_REVIEW（禁止自动批准）
- D-012 退款金额 > 实付金额 -> REJECT（API 层已挡，此处兜底）
"""
from dataclasses import dataclass

from app.core.config import get_settings

DECISION_APPROVE = "APPROVE"
DECISION_REJECT = "REJECT"
DECISION_HUMAN_REVIEW = "HUMAN_REVIEW"


@dataclass
class Decision:
    decision: str
    reason: str


class DecisionPolicy:
    def __init__(self):
        s = get_settings()
        self.amount_limit_cent = s.amount_limit_cent
        self.ocr_auto = s.ocr_confidence_auto
        self.ocr_review = s.ocr_confidence_review
        self.risk_auto_max = s.risk_auto_max
        self.risk_review_max = s.risk_review_max

    def decide(
        self,
        *,
        amount_cent: int,
        actual_amount_cent: int,
        ocr_confidence: float | None,
        evidence_status: str,
        fraud_score: float,
        sentiment_score: float,
        errors: list[str],
        # 订单三查结果（v2.0 §7/§8，资损红线，置于所有阈值之前）
        order_exists: bool = True,
        product_matched: bool = True,
        price_matched: bool = True,
        already_refunded: bool = False,
        order_verify_reason: str | None = None,
    ) -> Decision:
        # 订单三查优先（资损红线）：伪造/重复订单直接拒，商品/价格不一致转人工
        if not order_exists:
            return Decision(DECISION_REJECT, order_verify_reason or "订单不存在（疑似伪造）")
        if already_refunded:
            return Decision(DECISION_REJECT, order_verify_reason or "订单已售后，不可重复退款")
        if not product_matched or not price_matched:
            return Decision(
                DECISION_HUMAN_REVIEW, order_verify_reason or "订单商品/价格不一致，转人工复核"
            )

        # D-012 硬校验兜底
        if amount_cent > actual_amount_cent:
            return Decision(DECISION_REJECT, "退款金额超过订单实付金额（硬校验）")

        # 证据边界（01 文档业务边界）：未上传可识别凭证不自动退款
        if evidence_status == "EMPTY":
            return Decision(DECISION_HUMAN_REVIEW, "未上传可识别凭证")

        # D-004 模型/OCR 异常：绝不自动批准
        if errors:
            return Decision(DECISION_HUMAN_REVIEW, f"异常降级转人工: {'; '.join(errors[:3])}")

        # D-001 金额红线
        if amount_cent > self.amount_limit_cent:
            return Decision(DECISION_HUMAN_REVIEW, f"金额 {amount_cent / 100:.2f} 元超过限额")

        # D-002 OCR 置信度
        if evidence_status == "TIMEOUT":
            return Decision(DECISION_HUMAN_REVIEW, "OCR 超时")
        if ocr_confidence is not None and ocr_confidence < self.ocr_auto:
            if ocr_confidence < self.ocr_review:
                return Decision(DECISION_HUMAN_REVIEW, f"OCR 置信度过低 ({ocr_confidence:.2f})")
            return Decision(DECISION_HUMAN_REVIEW, f"OCR 置信度预警 ({ocr_confidence:.2f})")

        # D-003 风险分三段式
        # 区分「欺诈驱动高风险」与「舆情驱动高风险」：
        #   - 欺诈分高（fraud_score > 拒绝阈值）-> REJECT（恶意退款，防资损）
        #   - 仅舆情分高（sentiment_score 驱动 max）-> HUMAN_REVIEW（情绪激烈但未必恶意，
        #     需人工安抚决策，不误拒低风险单——需求文档 §2.2 G06 舆情升级 HIGH）
        risk = max(fraud_score, sentiment_score)
        if risk > self.risk_review_max:
            if fraud_score > self.risk_review_max:
                return Decision(DECISION_REJECT, f"欺诈风险分 {fraud_score:.2f} 超过拒绝阈值")
            return Decision(DECISION_HUMAN_REVIEW, f"舆情风险分 {risk:.2f} 高，需人工安抚决策")
        if risk > self.risk_auto_max:
            return Decision(DECISION_HUMAN_REVIEW, f"风险分 {risk:.2f} 需人工复核")

        return Decision(DECISION_APPROVE, "低风险自动通过")
