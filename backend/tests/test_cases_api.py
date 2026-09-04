"""案件接口测试：创建（202 异步）、金额硬校验、越权审批、状态校验、404。"""
import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.domain.models import (
    AgentRun,
    AuditLog,
    CaseEvidence,
    RefundCase,
    RefundRecord,
    ReviewTask,
    RiskAssessment,
)
from app.domain.status import CaseStatus
from app.main import app

client = TestClient(app)


def login(role: str) -> str:
    creds = {
        "MANAGER": ("manager", "manager123"),
        "CSR": ("csr", "csr123"),
    }
    username, password = creds[role]
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return r.json()["access_token"]


def auth_header(role: str) -> dict:
    return {"Authorization": f"Bearer {login(role)}"}


def create_case(role: str = "CSR", **overrides) -> dict:
    payload = {
        "applicant_id": "user-1",
        "order_id": "order-1",
        "applicant_amount": 12_800,
        "actual_amount": 12_800,
        "description": "商品破损，申请退款",
    }
    payload.update(overrides)
    r = client.post("/api/v1/cases", data=payload, headers=auth_header(role))
    assert r.status_code == 202, r.text
    return r.json()


@pytest.fixture(autouse=True)
def cleanup_created_cases():
    """测试用事务隔离成本高，这里简单清理本文件创建的测试数据（按外键依赖顺序）。"""
    yield
    db = SessionLocal()
    try:
        case_ids = [
            c.id
            for c in db.query(RefundCase)
            .filter(
                RefundCase.applicant_id == "user-1",
                RefundCase.idempotency_key.like("server-%"),
            )
            .all()
        ]
        if not case_ids:
            return
        for model in (AuditLog, CaseEvidence, ReviewTask, AgentRun, RiskAssessment, RefundRecord):
            db.query(model).filter(model.case_id.in_(case_ids)).delete(
                synchronize_session=False
            )
        db.query(RefundCase).filter(RefundCase.id.in_(case_ids)).delete(
            synchronize_session=False
        )
        db.commit()
    finally:
        db.close()


class TestCreateCase:
    def test_create_returns_202(self):
        data = create_case()
        assert data["status"] == "CREATED"
        assert data["ticket_no"].startswith("T")

    def test_amount_exceeds_actual_rejected(self):
        """裁决 D-012：退款金额 > 实付金额 -> 422，绝不进入流程。"""
        r = client.post(
            "/api/v1/cases",
            data={
                "applicant_id": "user-1",
                "order_id": "order-1",
                "applicant_amount": 50_000,
                "actual_amount": 30_000,
                "description": "多退",
            },
            headers=auth_header("CSR"),
        )
        assert r.status_code == 422
        assert r.json()["code"] == "AMOUNT_EXCEEDS"

    def test_negative_amount_rejected(self):
        r = client.post(
            "/api/v1/cases",
            data={
                "applicant_id": "user-1",
                "order_id": "order-1",
                "applicant_amount": -1,
                "actual_amount": 100,
            },
            headers=auth_header("CSR"),
        )
        assert r.status_code == 422


class TestGetCase:
    def test_get_case_detail(self):
        created = create_case()
        r = client.get(f"/api/v1/cases/{created['case_id']}", headers=auth_header("CSR"))
        assert r.status_code == 200
        assert r.json()["applicant_amount"] == 12_800

    def test_get_missing_case_404(self):
        r = client.get("/api/v1/cases/999999", headers=auth_header("CSR"))
        assert r.status_code == 404
        assert r.json()["code"] == "CASE_NOT_FOUND"


class TestDecision:
    def _run_workflow_suspend(self, case_id: int) -> None:
        """通过真实 LangGraph 工作流使案件挂起（金额>300 元路径）。"""
        from app.workflow.graph import run_workflow

        run_workflow(case_id, "api-test-suspend")

    def test_csr_cannot_approve(self):
        """角色校验：客服越权审批 -> 403。"""
        created = create_case(applicant_amount=35_000, actual_amount=35_000)
        self._run_workflow_suspend(created["case_id"])
        r = client.post(
            f"/api/v1/cases/{created['case_id']}/decision",
            json={"action": "APPROVE", "comment": "越权"},
            headers=auth_header("CSR"),
        )
        assert r.status_code == 403
        assert r.json()["code"] == "FORBIDDEN"

    def test_manager_approve_suspended(self):
        created = create_case(applicant_amount=35_000, actual_amount=35_000)
        self._run_workflow_suspend(created["case_id"])
        r = client.post(
            f"/api/v1/cases/{created['case_id']}/decision",
            json={"action": "APPROVE", "comment": "情况属实，批准退款"},
            headers=auth_header("MANAGER"),
        )
        assert r.status_code == 200
        # 审批 -> 唤醒图 -> APPROVED -> Mock 退款 -> COMPLETED
        assert r.json()["status"] == "COMPLETED"

    def test_approve_non_suspended_conflict(self):
        """状态校验：非 SUSPENDED 工单不可审批 -> 409。"""
        created = create_case()  # CREATED 状态
        r = client.post(
            f"/api/v1/cases/{created['case_id']}/decision",
            json={"action": "APPROVE"},
            headers=auth_header("MANAGER"),
        )
        assert r.status_code == 409
        assert r.json()["code"] == "INVALID_STATE"

    def test_decision_invalid_action(self):
        r = client.post(
            "/api/v1/cases/1/decision",
            json={"action": "MAYBE"},
            headers=auth_header("MANAGER"),
        )
        assert r.status_code == 422


class TestReviewTasks:
    def test_list_review_tasks(self):
        r = client.get("/api/v1/review-tasks", headers=auth_header("MANAGER"))
        assert r.status_code == 200
        assert isinstance(r.json(), list)
