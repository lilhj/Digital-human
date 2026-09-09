"""领域模型：7 张核心表（规格 docs/06 第 9 节 + Loop 提示词 Phase 2）。

金额一律整数分（BIGINT），禁止浮点。
"""
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.domain.status import CaseStatus


def _now() -> datetime:
    return datetime.now()


class OrderStatus(str, Enum):
    """订单状态机（8 态，v2.0 §6.2）。存库为字符串，便于审计与前端三态归并。"""

    PENDING_PAYMENT = "PENDING_PAYMENT"
    PAID = "PAID"
    SHIPPED = "SHIPPED"
    COMPLETED = "COMPLETED"
    REFUNDING = "REFUNDING"
    REFUNDED = "REFUNDED"
    EXCHANGING = "EXCHANGING"
    CLOSED = "CLOSED"


class RefundCase(Base):
    """退款案件主表：业务最终状态唯一真相（裁决 D-005）。"""

    __tablename__ = "refund_cases"
    __table_args__ = (
        Index("ix_refund_cases_status_assignee", "status", "assignee_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticket_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    applicant_id: Mapped[str] = mapped_column(String(64), nullable=False)  # 客户标识（MVP 字符串）
    assignee_id: Mapped[str | None] = mapped_column(String(64))  # 分派客服（Least Active）
    order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    applicant_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)  # 申报退款金额（分）
    actual_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)  # 订单实付金额（分）

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=CaseStatus.CREATED.value)
    fraud_score: Mapped[float | None] = mapped_column(Numeric(4, 3))
    sentiment_score: Mapped[float | None] = mapped_column(Numeric(4, 3))
    risk_score: Mapped[float | None] = mapped_column(Numeric(4, 3))
    decision: Mapped[str | None] = mapped_column(String(20))  # APPROVE / REJECT / HUMAN_REVIEW
    review_reason: Mapped[str | None] = mapped_column(String(512))  # 挂起/拒绝原因

    # 工单8 双层意图识别结果（前端大屏/案件详情展示；旧案件为 NULL）
    intent: Mapped[str | None] = mapped_column(String(20))  # REFUND / RETURN / EXCHANGE / UNKNOWN
    intent_source: Mapped[str | None] = mapped_column(String(20))  # rule / llm / fallback / fake
    intent_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    intent_fallback: Mapped[bool | None] = mapped_column(Boolean, default=False)  # True=转人工兜底

    # 工单8 交叉校验：买家端显式选择的售后类型（退款/退货退款/换货；员工侧/旧案件为 NULL）
    claim_type: Mapped[str | None] = mapped_column(String(20))

    description: Mapped[str] = mapped_column(Text, default="")  # 客诉描述（Fraud/Sentiment 输入）
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 乐观锁

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )

    evidences: Mapped[list["CaseEvidence"]] = relationship(back_populates="case")
    review_tasks: Mapped[list["ReviewTask"]] = relationship(back_populates="case")


class CaseEvidence(Base):
    """证据与 OCR 结果（裁决 D-002：置信度三级路由）。"""

    __tablename__ = "case_evidences"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    case_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("refund_cases.id"), nullable=False, index=True
    )
    image_url: Mapped[str] = mapped_column(String(512), nullable=False)
    file_hash: Mapped[str | None] = mapped_column(String(64))  # SHA-256
    ocr_text: Mapped[str | None] = mapped_column(Text)
    ocr_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    parse_status: Mapped[str] = mapped_column(String(20), default="OK")  # OK/TIMEOUT/LOW_CONFIDENCE

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    case: Mapped["RefundCase"] = relationship(back_populates="evidences")


class AgentRun(Base):
    """Agent 执行轨迹：大屏数据源 + 审计（规格 06 第 9 节）。"""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    case_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("refund_cases.id"), nullable=False, index=True
    )
    agent_name: Mapped[str] = mapped_column(String(32), nullable=False)  # INTAKE/EVIDENCE/FRAUD/SENTIMENT/DECISION
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # SUCCESS / FAILED
    input_json: Mapped[dict | None] = mapped_column(JSONB)
    output_json: Mapped[dict | None] = mapped_column(JSONB)
    error_tag: Mapped[str | None] = mapped_column(String(32))  # OCR_TIMEOUT/AI_PARSE_ERROR/...
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    # telemetry（工单5 可观测）：节点内 LLM 调用的累计 token（真实 usage，非 Fake/规则路径为 NULL）
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)


class RiskAssessment(Base):
    """风险评定结果：欺诈分/情绪分/风险等级 + 规则版本。"""

    __tablename__ = "risk_assessments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    case_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("refund_cases.id"), nullable=False, index=True
    )
    fraud_score: Mapped[float | None] = mapped_column(Numeric(4, 3))
    sentiment_score: Mapped[float | None] = mapped_column(Numeric(4, 3))
    risk_level: Mapped[str | None] = mapped_column(String(10))  # LOW / MEDIUM / HIGH
    rule_version: Mapped[str | None] = mapped_column(String(32))
    raw_json: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ReviewTask(Base):
    """人工审核任务（裁决 D-006：审批并发 409；D-011：Least Active 派单）。"""

    __tablename__ = "review_tasks"
    __table_args__ = (UniqueConstraint("case_id", name="uq_review_tasks_case_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    case_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("refund_cases.id"), nullable=False
    )
    assignee_id: Mapped[str | None] = mapped_column(String(64))  # 派给客服/主管
    status: Mapped[str] = mapped_column(String(20), default="PENDING")  # PENDING/APPROVED/REJECTED
    action: Mapped[str | None] = mapped_column(String(20))  # APPROVE / REJECT
    approver_id: Mapped[str | None] = mapped_column(String(64))
    comment: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )

    case: Mapped["RefundCase"] = relationship(back_populates="review_tasks")


class IdempotencyRecord(Base):
    """幂等记录：请求哈希 + 已保存响应（裁决 D-014：防资损幂等键）。"""

    __tablename__ = "idempotency_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA-256(请求体)
    response_json: Mapped[dict | None] = mapped_column(JSONB)  # 首次处理结果，重复请求直接返回
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class RefundRecord(Base):
    """退款流水（Mock）：独立幂等键防重复退款（裁决 D-014）。"""

    __tablename__ = "refund_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    case_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("refund_cases.id"), nullable=False, index=True
    )
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)  # 分
    status: Mapped[str] = mapped_column(String(20), default="PENDING")  # PENDING/SUCCESS/FAILED
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class User(Base):
    """用户（Phase 3 认证）：客服/主管/管理员。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # CSR / MANAGER / ADMIN
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class AuditLog(Base):
    """状态变更审计（规格 06 第 12 节：所有状态变更必须落审计）。"""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    case_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("refund_cases.id"), nullable=False, index=True
    )
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str | None] = mapped_column(String(20))
    operator: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


# ---------- 买家侧电商前台（v2.0 §6.1，金额一律 BIGINT 分） ----------


class Customer(Base):
    """买家（v2.0 §6.1）：手机号 + 密码登录。"""

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    nickname: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # 管理员停用后禁止登录
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Product(Base):
    """商品（v2.0 §6.1）：小米真实数据，product_id 为小米 ID 唯一。"""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    product_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)  # 小米 ID
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(64), default="")
    image_url: Mapped[str | None] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(default=True)


class CartItem(Base):
    """购物车条目（v2.0 §6.1）：关联买家与商品，联合唯一防重复。"""

    __tablename__ = "cart_items"
    __table_args__ = (
        UniqueConstraint("customer_id", "product_id", name="uq_cart_customer_product"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("customers.id"), nullable=False, index=True
    )
    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    customer: Mapped["Customer"] = relationship("Customer")
    product: Mapped["Product"] = relationship("Product")


class Order(Base):
    """订单（v2.0 §6.1）：下单锁定总额（快照），状态机 8 态。"""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    customer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("customers.id"), nullable=False, index=True
    )
    total_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=OrderStatus.PENDING_PAYMENT.value
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    paid_at: Mapped[datetime | None] = mapped_column()
    refunded_at: Mapped[datetime | None] = mapped_column()

    customer: Mapped["Customer"] = relationship("Customer")
    items: Mapped[list["OrderItem"]] = relationship(
        "OrderItem", back_populates="order", cascade="all, delete-orphan"
    )


class OrderItem(Base):
    """订单明细快照（v2.0 §6.1 + Q3）：下单那一刻的商品名/价格，弱关联 product_id 字符串。"""

    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("orders.id"), nullable=False, index=True
    )
    product_id: Mapped[str] = mapped_column(String(64), nullable=False)  # 弱关联，不建 FK
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    subtotal_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)

    order: Mapped["Order"] = relationship("Order", back_populates="items")
