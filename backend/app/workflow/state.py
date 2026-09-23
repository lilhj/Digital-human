"""LangGraph 工作流状态定义（Loop 提示词 Phase 5 建议字段）。"""
from typing import TypedDict


class RefundWorkflowState(TypedDict, total=False):
    # 输入
    case_id: int
    trace_id: str

    # 案件事实
    amount_cent: int
    actual_amount_cent: int
    description: str
    refund_count: int  # 用户历史退款次数（MVP 简化为 0）

    # Evidence 输出
    evidence_text: str
    ocr_confidence: float
    evidence_status: str  # OK / TIMEOUT / LOW_CONFIDENCE / NO_TEXT / EMPTY
    vision_description: str  # VL 图片语义理解（凭证一致性校验输入）
    vision_security_blocked: bool  # VL 描述命中 Critic BLOCK（图内注入）→ 转人工
    # VL 结构化字段透传（凭证一致性规则层判定输入，不只用 description 文本）
    vision_is_damaged: bool | None  # 图片是否有损伤
    vision_severity: str            # 轻微 / 中等 / 严重 / 报废级
    vision_category: str            # 产品品类（如 智能手机）
    vision_damage_type: str         # 损伤类型（如 屏幕碎裂）

    # 风险输出
    fraud_score: float
    fraud_features: list[str]
    sentiment_score: float
    risk_level: str
    risk_reason: str  # LLM 打分理由（工单5 锚定优化：风险等级与分数强一致 + 可追溯）
    evidence_consistent: str  # consistent / uncertain / inconsistent（LLM 兜底信号，不参与评分）
    evidence_penalty: float  # 恒 0（一致性惩罚已拆为独立信号，不污染 fraud_score）

    # 凭证一致性独立信号（规则优先，LLM 兜底；从 fraud_score 拆出，决策独立响应 + 前端独立展示）
    consistency_level: str          # MATCH / PARTIAL / MISMATCH / UNCERTAIN
    consistency_dimensions: list[str]  # 不符维度（可解释，如 "破损声明矛盾"）
    consistency_reason: str
    consistency_penalty: float      # 展示用惩罚分（0 / 0.15 / 0.30），不进 fraud_score

    # 订单三查（v2.0 §7/§8，order_verify 节点输出）
    order_exists: bool
    product_matched: bool
    price_matched: bool
    already_refunded: bool
    order_verify_reason: str
    order_verify_result: str  # PASS / REJECT / REVIEW

    # 安全网关（工单6：Critic 语义安检，intake 节点输出）
    security_risk_score: float
    security_action: str  # PASS / BLOCK
    security_matched: list[str]

    # 意图识别（工单8：双层意图识别，intent_node 输出）
    intent: str            # REFUND / RETURN / EXCHANGE / UNKNOWN
    intent_source: str     # rule / llm / fallback / fake
    intent_confidence: float
    intent_fallback: bool  # True=换货/意图不明，路由转人工复核

    # 决策
    decision: str  # APPROVE / REJECT / HUMAN_REVIEW
    review_reason: str
    errors: list[str]

    # 人工恢复
    human_action: str | None  # APPROVE / REJECT（resume 值）
    human_comment: str | None
    human_operator: str | None
