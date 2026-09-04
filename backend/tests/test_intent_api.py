"""工单8 意图识别 → 前端接入 端到端测试：

- intent_node 把识别结果落库 RefundCase（intent/intent_source/intent_confidence/intent_fallback）
- GET /api/v1/intent/summary 聚合运行期统计 + 红线评测指标
- GET /api/v1/cases/{id} 的 CaseDetailOut 暴露 intent 字段
"""
import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.domain.models import AgentRun, AuditLog, RefundCase
from app.main import app
from app.workflow import nodes as nodes_mod

client = TestClient(app)


def login(role: str = "CSR") -> str:
    creds = {"MANAGER": ("manager", "manager123"), "CSR": ("csr", "csr123")}
    username, password = creds[role]
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def auth_header(role: str = "CSR") -> dict:
    return {"Authorization": f"Bearer {login(role)}"}


@pytest.fixture
def make_case():
    r = client.post(
        "/api/v1/cases",
        data={
            "applicant_id": "intent-tester",
            "order_id": "order-intent-1",
            "applicant_amount": 12_800,
            "actual_amount": 12_800,
            "description": "商品破损，申请退款",  # 规则命中 REFUND
        },
        headers=auth_header(),
    )
    assert r.status_code == 202, r.text
    case_id = r.json()["case_id"]
    yield case_id
    db = SessionLocal()
    try:
        db.query(AgentRun).filter(AgentRun.case_id == case_id).delete(synchronize_session=False)
        db.query(AuditLog).filter(AuditLog.case_id == case_id).delete(synchronize_session=False)
        db.query(RefundCase).filter(RefundCase.id == case_id).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _run_intent(case_id: int, monkeypatch):
    """直接跑 intent_node（mock 掉 Redis 事件发布），落库意图结果。"""
    monkeypatch.setattr(nodes_mod, "publish_event", lambda *a, **k: None)
    return nodes_mod.intent_node({"case_id": case_id, "trace_id": "test"})


def test_intent_node_persists(make_case, monkeypatch):
    out = _run_intent(make_case, monkeypatch)
    db = SessionLocal()
    try:
        case = db.get(RefundCase, make_case)
        assert case.intent == out["intent"]
        assert case.intent_source is not None
        assert case.intent_confidence is not None
        assert case.intent_fallback in (True, False)
    finally:
        db.close()


def test_case_detail_exposes_intent(make_case, monkeypatch):
    _run_intent(make_case, monkeypatch)
    r = client.get(f"/api/v1/cases/{make_case}", headers=auth_header())
    assert r.status_code == 200
    body = r.json()
    assert body["intent"] in ("REFUND", "RETURN", "EXCHANGE", "UNKNOWN")
    assert body["intent_source"] is not None
    assert "intent_fallback" in body


def test_intent_summary_endpoint(make_case, monkeypatch):
    _run_intent(make_case, monkeypatch)
    r = client.get("/api/v1/intent/summary?range=all", headers=auth_header())
    assert r.status_code == 200
    body = r.json()
    assert body["total_identified"] >= 1
    assert any(d["intent"] == "REFUND" for d in body["distribution"])
    assert body["to_human"] >= 0
    # 红线评测指标来自离线基准
    assert "eval" in body
    assert body["eval"]["recall"] >= 0.9
    assert body["eval"]["hallucination_rate"] <= 0.02
    assert body["eval"]["token_reduction"] >= 0.4


def test_intent_summary_bad_range(make_case):
    r = client.get("/api/v1/intent/summary?range=forever", headers=auth_header())
    assert r.status_code == 400
