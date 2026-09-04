"""订单三查 + 防重复退款 + 回写闭环 测试（v2.0 §7/§8/§9）。

强制使用 DbOrderVerifyProvider（直查真实 Order/OrderItem），验证：
1. 伪造订单号 -> REJECT
2. 已售后订单二次申请 -> REJECT（防重复退款）
3. 订单明细缺失 -> HUMAN_REVIEW（转人工）
4. 真实订单三查通过 -> 正常决策；退款通过后订单状态回写 REFUNDED
"""
import uuid

import pytest

from app.core.database import SessionLocal
from app.domain.models import (
    CaseEvidence,
    Customer,
    Order,
    OrderItem,
    OrderStatus,
    RefundCase,
)
from app.domain.status import CaseStatus
from app.workflow import nodes
from app.workflow.graph import run_workflow, resume_workflow
from app.workflow.order_verify import DbOrderVerifyProvider


@pytest.fixture(autouse=True)
def use_db_verifier():
    """强制订单三查走真实查库（覆盖默认 Fake）。"""
    prev = nodes.order_verify_provider
    nodes.order_verify_provider = DbOrderVerifyProvider()
    yield
    nodes.order_verify_provider = prev


def _make_customer(phone: str, db: SessionLocal) -> Customer:
    c = db.query(Customer).filter_by(phone=phone).first()
    if c is None:
        c = Customer(phone=phone, password_hash="x", nickname="t")
        db.add(c)
        db.flush()
    return c


def _make_order(db: SessionLocal, *, order_no: str, total_cents: int,
                items: list[dict] | None = None, status=OrderStatus.PAID.value) -> Order:
    o = db.query(Order).filter_by(order_no=order_no).first()
    if o is None:
        o = Order(order_no=order_no, customer_id=1, total_cents=total_cents, status=status)
        db.add(o)
        db.flush()
    if items is not None:
        for it in items:
            db.add(OrderItem(order_id=o.id, **it))
    db.commit()
    return o


def _make_case(order_no: str, amount: int, marker: str, with_evidence: bool = False) -> int:
    db = SessionLocal()
    try:
        case = RefundCase(
            ticket_no=f"T{uuid.uuid4().hex[:10].upper()}",
            applicant_id=marker,
            order_id=order_no,
            applicant_amount=amount,
            actual_amount=amount,
            description="商品破损，申请退款",
            status=CaseStatus.CREATED.value,
            idempotency_key=f"ov-{uuid.uuid4().hex}",
        )
        db.add(case)
        db.flush()
        if with_evidence:
            db.add(CaseEvidence(case_id=case.id, image_url="uploads/test-receipt.jpg",
                                parse_status="OK"))
        db.commit()
        return case.id
    finally:
        db.close()


def _status(case_id: int) -> str:
    db = SessionLocal()
    try:
        return db.get(RefundCase, case_id).status
    finally:
        db.close()


def _order_status(order_no: str) -> str | None:
    db = SessionLocal()
    try:
        o = db.query(Order).filter_by(order_no=order_no).first()
        return o.status if o else None
    finally:
        db.close()


MARKER = f"ov-test-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def cleanup():
    yield
    db = SessionLocal()
    try:
        # 1) 清理案件（applicant 可能是 MARKER 本身，或带标记的手机号 `1-x`/`2-x`；
        #    cleanup_cases_by_marker 只认 == MARKER，会漏掉越权测试的案子，故内联覆盖）
        phones = [f"1{MARKER[-9:]}", f"2{MARKER[-9:]}"]
        case_ids = [
            c.id
            for c in db.query(RefundCase)
            .filter(RefundCase.applicant_id.in_([MARKER, *phones]))
            .all()
        ]
        if case_ids:
            from app.domain.models import (
                AgentRun,
                AuditLog,
                RefundRecord,
                ReviewTask,
                RiskAssessment,
            )

            for model in (AuditLog, CaseEvidence, ReviewTask, AgentRun, RiskAssessment, RefundRecord):
                db.query(model).filter(model.case_id.in_(case_ids)).delete(
                    synchronize_session=False
                )
            db.query(RefundCase).filter(RefundCase.id.in_(case_ids)).delete(
                synchronize_session=False
            )

        # 2) 清理订单（明细 -> 订单）。用批量 DELETE 而不是 db.delete(o)：
        #    对象删除只在 flush 时落库，会连带把后面的买家删除撞到 orders FK 上。
        order_ids = [
            o.id for o in db.query(Order).filter(Order.order_no.like(f"OV-{MARKER}-%")).all()
        ]
        if order_ids:
            db.query(OrderItem).filter(OrderItem.order_id.in_(order_ids)).delete(
                synchronize_session=False
            )
            db.query(Order).filter(Order.id.in_(order_ids)).delete(
                synchronize_session=False
            )

        # 3) 清理买家
        db.query(Customer).filter(Customer.phone.in_(phones)).delete(
            synchronize_session=False
        )
        db.commit()
    finally:
        db.close()


class TestOrderVerify:
    def test_fake_order_rejected(self):
        """伪造订单号（库里没有）-> REJECT。"""
        case_id = _make_case("OV-NONEXISTENT", 12_800, MARKER)
        result = run_workflow(case_id, f"tr-{uuid.uuid4().hex}")
        assert result["decision"] == "REJECT"
        assert _status(case_id) == CaseStatus.REJECTED.value

    def test_already_refunded_rejected(self):
        """已退款订单再次申请 -> REJECT（防重复退款）。"""
        db = SessionLocal()
        try:
            cust = _make_customer(f"1{MARKER[-9:]}", db)
            db.commit()
            _make_order(db, order_no=f"OV-{MARKER}-R", total_cents=12_800,
                        items=[{"product_id": "P1", "product_name": "商品",
                                "price_cents": 12_800, "quantity": 1, "subtotal_cents": 12_800}],
                        status=OrderStatus.REFUNDED.value)
        finally:
            db.close()
        case_id = _make_case(f"OV-{MARKER}-R", 12_800, MARKER)
        result = run_workflow(case_id, f"tr-{uuid.uuid4().hex}")
        assert result["decision"] == "REJECT"
        assert "不可重复退款" in result.get("review_reason", "")

    def test_missing_items_review(self):
        """订单明细缺失 -> HUMAN_REVIEW（转人工，不自动放行）。"""
        db = SessionLocal()
        try:
            cust = _make_customer(f"1{MARKER[-9:]}", db)
            db.commit()
            _make_order(db, order_no=f"OV-{MARKER}-M", total_cents=12_800, items=[])
        finally:
            db.close()
        case_id = _make_case(f"OV-{MARKER}-M", 12_800, MARKER, with_evidence=True)
        result = run_workflow(case_id, f"tr-{uuid.uuid4().hex}")
        assert result["decision"] == "HUMAN_REVIEW"
        assert _status(case_id) == CaseStatus.SUSPENDED.value

    def test_real_order_pass_and_writeback(self):
        """真实订单三查通过 -> 自动退款 -> 订单状态回写 REFUNDED。"""
        db = SessionLocal()
        try:
            cust = _make_customer(f"1{MARKER[-9:]}", db)
            db.commit()
            _make_order(db, order_no=f"OV-{MARKER}-P", total_cents=12_800,
                        items=[{"product_id": "P1", "product_name": "商品",
                                "price_cents": 12_800, "quantity": 1, "subtotal_cents": 12_800}])
        finally:
            db.close()
        case_id = _make_case(f"OV-{MARKER}-P", 12_800, MARKER, with_evidence=True)
        result = run_workflow(case_id, f"tr-{uuid.uuid4().hex}")
        assert result["decision"] == "APPROVE"
        assert _status(case_id) == CaseStatus.COMPLETED.value
        # 回写闭环：订单状态与退款状态一致
        assert _order_status(f"OV-{MARKER}-P") == OrderStatus.REFUNDED.value

    def test_order_not_owned_rejected(self):
        """M-1 越权防护：订单属于他人买家 -> REJECT（纵深防御，非本人不可退款）。"""
        db = SessionLocal()
        try:
            # 订单属主（2<marker>）与申请人（1<marker>）是两个不同买家
            owner = _make_customer(f"2{MARKER[-9:]}", db)
            other = _make_customer(f"1{MARKER[-9:]}", db)
            o = Order(order_no=f"OV-{MARKER}-O", customer_id=owner.id,
                      total_cents=12_800, status=OrderStatus.PAID.value)
            db.add(o)
            db.flush()
            db.add(OrderItem(order_id=o.id, product_id="P1", product_name="商品",
                             price_cents=12_800, quantity=1, subtotal_cents=12_800))
            db.commit()
        finally:
            db.close()
        # 申请人用他人订单号申请退款
        case_id = _make_case(f"OV-{MARKER}-O", 12_800, f"1{MARKER[-9:]}")
        result = run_workflow(case_id, f"tr-{uuid.uuid4().hex}")
        assert result["decision"] == "REJECT"
        assert "越权" in result.get("review_reason", "")
