"""M-1 越权防护：BUYER 建单必须本人名下订单（API 层 + 归属校验的端到端验证）。

- BUYER 令牌建单：applicant_id 强制为 token 手机号本人，不信任表单；
- 订单必须属于该买家，否则 403 ORDER_NOT_OWNED；
- STAFF 代客建单不受限（order_verify 兜底仍会拦截越权）。
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.domain.models import (
    AuditLog,
    CaseEvidence,
    Customer,
    Order,
    OrderItem,
    RefundCase,
    RefundRecord,
)
from app.main import app

client = TestClient(app)

MARKER = f"own-{uuid.uuid4().hex[:8]}"
PHONE_A = f"199{MARKER[-8:]}"
PHONE_B = f"188{MARKER[-8:]}" + "0"


def register_buyer(phone: str) -> str:
    """注册买家并返回 BUYER 令牌。"""
    r = client.post(
        "/api/v1/buyer/auth/register",
        json={"phone": phone, "password": "pass123456", "nickname": "测试买家"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _insert_order_for(phone: str, total: int = 12_800) -> int:
    """直接为某买家插入一笔订单（跳过购物车/下单链路，聚焦建单越权）。"""
    db = SessionLocal()
    try:
        cust = db.query(Customer).filter_by(phone=phone).first()
        assert cust is not None
        o = Order(
            order_no=f"OWN-{uuid.uuid4().hex[:10].upper()}",
            customer_id=cust.id,
            total_cents=total,
            status="PAID",
        )
        db.add(o)
        db.flush()
        db.add(
            OrderItem(
                order_id=o.id, product_id="P1", product_name="商品",
                price_cents=total, quantity=1, subtotal_cents=total,
            )
        )
        db.commit()
        return o.id
    finally:
        db.close()


@pytest.fixture(autouse=True)
def cleanup() -> None:
    yield
    db = SessionLocal()
    try:
        # 按 phone 反查本案建立的数据并清理（依赖顺序：明细->订单->买家->案件）
        for phone in (PHONE_A, PHONE_B):
            cust = db.query(Customer).filter_by(phone=phone).first()
            if cust is None:
                continue
            order_ids = [o.id for o in db.query(Order).filter_by(customer_id=cust.id).all()]
            if order_ids:
                db.query(OrderItem).filter(OrderItem.order_id.in_(order_ids)).delete(
                    synchronize_session=False
                )
                db.query(Order).filter(Order.id.in_(order_ids)).delete(
                    synchronize_session=False
                )
            case_ids = [
                c.id for c in db.query(RefundCase).filter_by(applicant_id=phone).all()
            ]
            if case_ids:
                for model in (AuditLog, CaseEvidence, RefundRecord):
                    db.query(model).filter(model.case_id.in_(case_ids)).delete(
                        synchronize_session=False
                    )
                db.query(RefundCase).filter(RefundCase.id.in_(case_ids)).delete(
                    synchronize_session=False
                )
            db.delete(cust)
        db.commit()
    finally:
        db.close()


def _post_create_case(token: str, applicant_id: str, order_id: str | int) -> object:
    return client.post(
        "/api/v1/cases",
        data={
            "applicant_id": applicant_id,
            "order_id": str(order_id),
            "applicant_amount": 12_800,
            "actual_amount": 12_800,
            "description": "商品破损，申请退款",
        },
        headers={"Authorization": f"Bearer {token}"},
    )


class TestBuyerOwnership:
    def test_buyer_forces_applicant_and_rejects_other_order(self):
        """BUYER 令牌：表单伪造他人 applicant_id 无效；他人订单 -> 403。"""
        register_buyer(PHONE_A)             # 买家 A（自有订单的属主）
        token_b = register_buyer(PHONE_B)   # 买家 B（想要越权）
        order_a = _insert_order_for(PHONE_A)

        # B 持自己 token、拿 A 的订单号建单（applicant 会强制为 B 本人）
        r = _post_create_case(token_b, applicant_id="someone-else", order_id=order_a)
        assert r.status_code == 403, r.text
        body = r.json()
        assert body["code"] == "ORDER_NOT_OWNED"

    def test_buyer_own_order_ok_and_applicant_forced(self):
        """BUYER 用自己订单建单 -> 202；applicant_id 强制为 token 本人。"""
        token_a = register_buyer(PHONE_A)
        order_a = _insert_order_for(PHONE_A)

        r = _post_create_case(token_a, applicant_id="someone-else", order_id=order_a)
        assert r.status_code == 202, r.text
        case_id = r.json()["case_id"]

        db = SessionLocal()
        try:
            case = db.get(RefundCase, case_id)
            assert case is not None
            # 关键：applicant_id 必须等于 token 手机号，忽略表单伪造值
            assert case.applicant_id == PHONE_A
            assert case.order_id == str(order_a)
        finally:
            db.close()


class TestStaffUnaffected:
    def test_staff_can_create_for_any_order(self):
        """STAFF（客服代客建单）不受 BUYER 归属强制，仍可建单（由 order_verify 兜底）。"""
        from app.domain.models import User

        db = SessionLocal()
        try:
            staff = db.query(User).filter_by(username="csr").first()
            assert staff is not None
        finally:
            db.close()

        r = client.post(
            "/api/v1/auth/login",
            json={"username": "csr", "password": "csr123"},
        )
        assert r.status_code == 200
        token = r.json()["access_token"]

        r = client.post(
            "/api/v1/cases",
            data={
                "applicant_id": "非注册手机号-测试",
                "order_id": "ORD-NOT-EXIST-NOW",
                "applicant_amount": 100,
                "actual_amount": 100,
                "description": "客服代录",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 202, r.text
        # 清理本次 STAFF 建单（该用例不归属任何测试买家，不能靠全局 cleanup）
        case_id = r.json()["case_id"]
        db = SessionLocal()
        try:
            for model in (AuditLog, CaseEvidence, RefundRecord):
                db.query(model).filter(model.case_id == case_id).delete(
                    synchronize_session=False
                )
            db.query(RefundCase).filter(RefundCase.id == case_id).delete(
                synchronize_session=False
            )
            db.commit()
        finally:
            db.close()