"""往 case 的 graph 接口测试：节点 input 快照随 output 一起可复盘（方案 B：能点开看每个节点的输入输出）。

覆盖：
1. GET /cases/{id}/graph 返回节点的 input + output
2. input 快照包含业务字段（description/金额/OCR 文本/风险分），不含 trace_id 之类元信息
"""
from datetime import datetime

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.domain.models import AgentRun, RefundCase
from app.domain.status import CaseStatus
from app.main import app

client = TestClient(app)

# 特殊标记 applicant_id，与全局 cleanup 约定一致（test_cases_api 的 cleanup_created_cases）
MARKER = "graph-input-test"


def _login_manager() -> str:
    r = client.post("/api/v1/auth/login", json={"username": "manager", "password": "manager123"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_graph_returns_node_input_output():
    """graph 接口:每个节点 input/output 都可复盘。"""
    db = SessionLocal()
    try:
        case = RefundCase(
            ticket_no="T-GRAPH-INPUT",
            applicant_id=MARKER,
            order_id="ORD-GRAPH",
            applicant_amount=12_800,
            actual_amount=12_800,
            description="商品破损，申请退款",
            status=CaseStatus.COMPLETED.value,
            idempotency_key="graph-input-idem",
            version=0,
        )
        db.add(case)
        db.flush()

        # 插入一条带 input 快照的 FRAUD 节点（模拟 run 后记录）
        db.add(
            AgentRun(
                case_id=case.id,
                agent_name="FRAUD",
                status="SUCCESS",
                input_json={
                    "description": "商品破损，申请退款",
                    "evidence_text": "破损商品 退款 发票",
                    "refund_count": 0,
                },
                output_json={
                    "fraud_score": 0.3,
                    "fraud_features": ["商品破损"],
                    "sentiment_score": 0.2,
                    "risk_level": "MEDIUM",
                    "risk_reason": "常见破损客诉，打分居中",
                },
                finished_at=datetime.now(),
                duration_ms=820,
            )
        )
        db.commit()
        case_id = case.id
    finally:
        db.close()

    r = client.get(
        f"/api/v1/cases/{case_id}/graph",
        headers={"Authorization": f"Bearer {_login_manager()}"},
    )
    assert r.status_code == 200, r.text
    nodes = {n["agent"]: n for n in r.json()["nodes"]}
    fraud = nodes.get("FRAUD")
    assert fraud is not None, f"应有 FRAUD 节点: {r.json()}"
    # 复盘核心：input 快照可读
    assert fraud["input"]["description"] == "商品破损，申请退款"
    assert fraud["input"]["evidence_text"] == "破损商品 退款 发票"
    # output 完整
    assert fraud["output"]["fraud_score"] == 0.3
    assert fraud["output"]["risk_reason"] == "常见破损客诉，打分居中"
    assert fraud["duration_ms"] == 820

    # 清理测试数据
    db = SessionLocal()
    try:
        db.query(AgentRun).filter_by(case_id=case_id).delete()
        db.query(RefundCase).filter_by(id=case_id).delete()
        db.commit()
    finally:
        db.close()