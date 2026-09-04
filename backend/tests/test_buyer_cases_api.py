"""买家「我的售后」接口单测：GET /api/v1/buyer/cases。

「我的售后」以后端 PG 为真相（不再依赖前端 localStorage 镜像）：
1. 买家登录后能查到本人案件（ticket_no/order_id/八态 status/decision 等字段完整）；
2. 只能看到本人案件，他人不可见（越权防护）；
3. 员工 token 无权访问（get_current_customer 拒绝非 BUYER）。
"""
import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.domain.models import (
    AuditLog,
    CartItem,
    CaseEvidence,
    Customer,
    Order,
    OrderItem,
    OrderStatus,
    RefundCase,
)
from app.infrastructure.storage import UPLOAD_DIR
from app.main import app

client = TestClient(app)

MARKER = f"bc-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def cleanup():
    yield
    db = SessionLocal()
    try:
        cust_ids = [c.id for c in db.query(Customer).filter(Customer.phone.like(f"{MARKER}%")).all()]
        case_ids = [c.id for c in db.query(RefundCase).filter(RefundCase.applicant_id.like(f"{MARKER}%")).all()]
        order_ids = [o.id for o in db.query(Order).filter(Order.customer_id.in_(cust_ids)).all()]
        # 先取凭证 URL（随后才删 CaseEvidence 行），并清掉测试落盘的 uploads 文件
        ev_urls = [
            url
            for (url,) in db.query(CaseEvidence.image_url)
            .filter(CaseEvidence.case_id.in_(case_ids))
            .all()
            if url
        ]
        for cid in case_ids:
            for model in (AuditLog, CaseEvidence):
                db.query(model).filter(model.case_id == cid).delete(synchronize_session=False)
        db.query(RefundCase).filter(RefundCase.id.in_(case_ids)).delete(synchronize_session=False)
        for url in ev_urls:
            (UPLOAD_DIR / url.rstrip("/").rsplit("/", 1)[-1]).unlink(missing_ok=True)
        if order_ids:
            db.query(OrderItem).filter(OrderItem.order_id.in_(order_ids)).delete(synchronize_session=False)
            db.query(Order).filter(Order.id.in_(order_ids)).delete(synchronize_session=False)
        if cust_ids:
            db.query(CartItem).filter(CartItem.customer_id.in_(cust_ids)).delete(synchronize_session=False)
            db.query(Customer).filter(Customer.id.in_(cust_ids)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _register(phone: str) -> tuple[str, str]:
    r = client.post(
        "/api/v1/buyer/auth/register",
        json={"phone": phone, "password": "pass123456", "nickname": "售后测试"},
    )
    assert r.status_code in (200, 201), r.text
    return phone, r.json()["access_token"]


def _create_paid_order(phone: str, token: str) -> dict:
    """为买家建一张真实订单（供 M-1 越权校验通过后建案件）。"""
    r = client.post(
        "/api/v1/cart",
        json={"product_id": 24648, "quantity": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code in (200, 201), r.text
    cart = client.get("/api/v1/cart", headers={"Authorization": f"Bearer {token}"})
    ids = [i["id"] for i in cart.json()["items"]]
    o = client.post(
        "/api/v1/orders",
        json={"cart_item_ids": ids},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert o.status_code == 201, o.text
    return o.json()


def _create_case(phone: str, order_id, token: str) -> dict:
    r = client.post(
        "/api/v1/cases",
        data={
            "applicant_id": phone,
            "order_id": str(order_id),
            "applicant_amount": 479_900,
            "actual_amount": 479_900,
            "description": "退款：商品破损无法使用",
        },
        files={"image": (f"{MARKER}-receipt.png", b"\x89PNG\r\n\x1a\nfakepng", "image/png")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 202, r.text
    return r.json()


def _staff_token() -> str:
    r = client.post("/api/v1/auth/login", json={"username": "csr", "password": "csr123"})
    assert r.status_code == 200
    return r.json()["access_token"]


def test_buyer_lists_own_cases_with_full_fields():
    phone_a = f"{MARKER}A-{uuid.uuid4().hex[:6]}"
    _, tok_a = _register(phone_a)
    order = _create_paid_order(phone_a, tok_a)
    created = _create_case(phone_a, order["id"], tok_a)

    r = client.get("/api/v1/buyer/cases", headers={"Authorization": f"Bearer {tok_a}"})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert any(it["id"] == created["case_id"] for it in items)

    mine = next(it for it in items if it["id"] == created["case_id"])
    assert mine["ticket_no"] == created["ticket_no"]
    assert mine["order_id"] == str(order["id"])
    assert mine["status"] == "CREATED"  # TESTING=1 不启 Worker，案件停在受理态
    assert mine["decision"] is None  # 未裁决
    assert mine["applicant_amount"] == 479_900
    assert mine["actual_amount"] == 479_900
    assert "破损" in mine["description"]
    assert mine["evidence_url"] is not None and mine["evidence_url"].startswith("/uploads/")
    assert mine["created_at"] is not None


def test_buyer_cannot_see_other_buyers_cases():
    phone_a = f"{MARKER}B-{uuid.uuid4().hex[:6]}"
    phone_b = f"{MARKER}C-{uuid.uuid4().hex[:6]}"
    _, tok_a = _register(phone_a)
    _, tok_b = _register(phone_b)
    order = _create_paid_order(phone_a, tok_a)
    _create_case(phone_a, order["id"], tok_a)

    # 买家 A 能看到 1 条；买家 B 应为空
    r_a = client.get("/api/v1/buyer/cases", headers={"Authorization": f"Bearer {tok_a}"})
    assert len(r_a.json()["items"]) == 1
    r_b = client.get("/api/v1/buyer/cases", headers={"Authorization": f"Bearer {tok_b}"})
    assert r_b.status_code == 200 and r_b.json()["items"] == []


def test_staff_token_rejected_on_buyer_cases():
    r = client.get("/api/v1/buyer/cases", headers={"Authorization": f"Bearer {_staff_token()}"})
    assert r.status_code == 403


def test_completed_case_surfaces_real_decision_and_refunded_order():
    """后端处理完成后的真实判定能透出到买家侧（前端「已完结+处理结果」据此渲染）。

    直接把案件推进到终态等价于工作流 finalize 的结果（其他测试已验证工作流会写这些字段）：
    案件 COMPLETED + decision=APPROVE + review_reason，订单 REFUNDED。
    断言：GET /buyer/cases 返回真实裁决，GET /orders 返回 REFUNDED。
    """
    phone = f"{MARKER}D-{uuid.uuid4().hex[:6]}"
    _, tok = _register(phone)
    order = _create_paid_order(phone, tok)
    created = _create_case(phone, order["id"], tok)

    db = SessionLocal()
    try:
        case = db.get(RefundCase, created["case_id"])
        assert case is not None
        case.status = "COMPLETED"
        case.decision = "APPROVE"
        case.review_reason = "低风险自动通过"
        ord_row = db.query(Order).filter_by(id=order["id"]).one()
        ord_row.status = OrderStatus.REFUNDED.value
        ord_row.refunded_at = datetime.now()
        db.commit()
    finally:
        db.close()

    cases = client.get("/api/v1/buyer/cases", headers={"Authorization": f"Bearer {tok}"}).json()["items"]
    mine = next(it for it in cases if it["id"] == created["case_id"])
    assert mine["status"] == "COMPLETED"
    assert mine["decision"] == "APPROVE"
    assert mine["review_reason"] == "低风险自动通过"

    orders = client.get("/api/v1/orders", headers={"Authorization": f"Bearer {tok}"}).json()["items"]
    assert orders[0]["id"] == order["id"]
    assert orders[0]["status"] == "REFUNDED"