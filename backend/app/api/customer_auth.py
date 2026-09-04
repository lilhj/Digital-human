"""买家认证接口（v2.0 §14 买家鉴权）：注册 / 登录，签发 type=BUYER 的 JWT。"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.schemas import BuyerAuthResponse, BuyerLoginRequest, BuyerRegisterRequest
from app.core.database import get_db
from app.core.security import create_buyer_token, get_current_customer, hash_password, verify_password
from app.domain.models import CaseEvidence, Customer, RefundCase

router = APIRouter(prefix="/api/v1/buyer/auth", tags=["buyer-auth"])


@router.post("/register", response_model=BuyerAuthResponse, status_code=201)
def register(body: BuyerRegisterRequest, db: Annotated[Session, Depends(get_db)]):
    if db.query(Customer).filter_by(phone=body.phone).first() is not None:
        raise HTTPException(
            status_code=409, detail={"code": "PHONE_TAKEN", "message": "该手机号已注册"}
        )
    customer = Customer(
        phone=body.phone,
        password_hash=hash_password(body.password),
        nickname=body.nickname,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return BuyerAuthResponse(
        access_token=create_buyer_token(customer),
        phone=customer.phone,
        nickname=customer.nickname,
    )


@router.post("/login", response_model=BuyerAuthResponse)
def login(body: BuyerLoginRequest, db: Annotated[Session, Depends(get_db)]):
    customer = db.query(Customer).filter_by(phone=body.phone).first()
    if customer is None or not verify_password(body.password, customer.password_hash):
        raise HTTPException(
            status_code=401, detail={"code": "BAD_CREDENTIALS", "message": "手机号或密码错误"}
        )
    return BuyerAuthResponse(
        access_token=create_buyer_token(customer),
        phone=customer.phone,
        nickname=customer.nickname,
    )


# ---------- 买家本人售后案件（v2.0 §16） ----------
# 「我的售后」以 PG 为真相，不再依赖前端 localStorage 本地镜像：
# 案件八态、人工判定（decision/review_reason）由这里统一透出，前端据此映射状态+处理结果。

buyer_cases_router = APIRouter(prefix="/api/v1/buyer/cases", tags=["buyer-cases"])


@buyer_cases_router.get("")
def list_my_cases(
    db: Annotated[Session, Depends(get_db)],
    customer: Annotated[Customer, Depends(get_current_customer)],
):
    """买家查询本人退款/换货案件（applicant_id = 手机号，M-1 越权防护保证归属）。

    返回「我的售后」所需的紧凑字段：工单号/订单号/八态状态/判定结果
    （decision + review_reason 为后端真实裁决）/申报与实付金额/诉求描述/首张凭证图 URL。
    """
    cases = (
        db.query(RefundCase)
        .filter_by(applicant_id=customer.phone)
        .order_by(RefundCase.id.desc())
        .limit(200)
        .all()
    )
    if not cases:
        return {"items": []}
    ev_map = {
        cid: url
        for cid, url in db.query(CaseEvidence.case_id, CaseEvidence.image_url)
        .filter(CaseEvidence.case_id.in_([c.id for c in cases]))
        .all()
    }
    return {
        "items": [
            {
                "id": c.id,
                "ticket_no": c.ticket_no,
                "order_id": c.order_id,
                "status": c.status,
                "decision": c.decision,
                "review_reason": c.review_reason,
                "applicant_amount": c.applicant_amount,
                "actual_amount": c.actual_amount,
                "description": c.description,
                "claim_type": c.claim_type,
                "evidence_url": ev_map.get(c.id),
                "created_at": c.created_at,
            }
            for c in cases
        ]
    }
