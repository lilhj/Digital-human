"""API 请求/响应模型（Pydantic v2）。金额一律以分为单位的 int。"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    display_name: str


class CreateCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applicant_id: str = Field(min_length=1, max_length=64)
    order_id: str = Field(min_length=1, max_length=64)
    applicant_amount: int = Field(ge=0, description="申报退款金额（分）")
    actual_amount: int = Field(ge=0, description="订单实付金额（分）")
    description: str = Field(default="", max_length=2000)


class CreateCaseResponse(BaseModel):
    case_id: int
    ticket_no: str
    status: str
    message: str = "已受理，异步处理中"


class CaseEvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    image_url: str | None = None
    ocr_text: str | None = None
    ocr_confidence: float | None = None
    parse_status: str | None = None


class ReviewTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: int
    assignee_id: str | None = None
    status: str
    comment: str | None = None
    created_at: datetime


class CaseDetailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticket_no: str
    order_id: str
    applicant_id: str  # 买家标识（前端脱敏显示）
    applicant_amount: int
    actual_amount: int
    status: str
    fraud_score: float | None = None
    sentiment_score: float | None = None
    risk_score: float | None = None
    decision: str | None = None
    review_reason: str | None = None
    # 工单8 双层意图识别结果（前端大屏/案件详情展示；旧案件为 None）
    intent: str | None = None
    intent_source: str | None = None
    intent_confidence: float | None = None
    intent_fallback: bool | None = None
    claim_type: str | None = None  # 买家端显式选择的售后类型（退款/退货退款/换货）
    description: str = ""  # 买家诉求（前端展示）
    created_at: datetime
    updated_at: datetime
    evidences: list[CaseEvidenceOut] = []


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(pattern="^(APPROVE|REJECT)$", description="APPROVE 或 REJECT")
    comment: str = Field(default="", max_length=1000)


class DecisionResponse(BaseModel):
    case_id: int
    status: str
    message: str


# ---------- 买家侧（v2.0 §14） ----------


class BuyerRegisterRequest(BaseModel):
    phone: str = Field(min_length=6, max_length=20)
    password: str = Field(min_length=6, max_length=128)
    nickname: str | None = Field(default=None, max_length=64)


class BuyerLoginRequest(BaseModel):
    phone: str = Field(min_length=6, max_length=20)
    password: str = Field(min_length=1, max_length=128)


class BuyerAuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    phone: str
    nickname: str | None = None


# ---------- ADMIN 用户管理（phase12） ----------


class AdminUserOut(BaseModel):
    """员工信息。绝不返回 password_hash。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str  # CSR / MANAGER / ADMIN
    display_name: str
    is_active: bool
    created_at: datetime


class AdminCustomerOut(BaseModel):
    """买家信息。绝不返回 password_hash。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    phone: str
    nickname: str | None = None
    is_active: bool
    created_at: datetime


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=6, max_length=128)
    role: str = Field(pattern="^(CSR|MANAGER|ADMIN)$")
    display_name: str = Field(min_length=1, max_length=64)


class UpdateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str | None = Field(default=None, pattern="^(CSR|MANAGER|ADMIN)$")
    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    is_active: bool | None = None


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_password: str = Field(min_length=6, max_length=128)


class AdminActionResponse(BaseModel):
    ok: bool = True
    message: str


class CartAddRequest(BaseModel):
    product_id: int = Field(description="小米商品 ID")
    quantity: int = Field(ge=1, le=99, default=1)


class CartUpdateRequest(BaseModel):
    quantity: int = Field(ge=1, le=99)


class CreateOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cart_item_ids: list[int] = Field(min_length=1, description="购物车条目 ID 列表（后端以购物车为真相，下单后清空）")


class OrderItemOut(BaseModel):
    product_id: str
    product_name: str
    price_cents: int
    quantity: int
    subtotal_cents: int


class OrderOut(BaseModel):
    id: int
    order_no: str
    total_cents: int
    status: str
    created_at: datetime
    paid_at: datetime | None = None
    refunded_at: datetime | None = None
    items: list[OrderItemOut] = []
