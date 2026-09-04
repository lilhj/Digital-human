"""Phase 7 测试：审批幂等、并发审批互斥、锁超时释放、退款动作防重。"""
import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.domain.models import IdempotencyRecord, RefundCase, RefundRecord
from app.domain.status import CaseStatus
from app.infrastructure.idempotency import build_key, execute_idempotent, hash_request
from app.infrastructure.lock import DistributedLock
from app.infrastructure.streams import get_redis
from app.main import app
from app.workflow.graph import run_workflow
from tests.conftest import cleanup_cases_by_marker

MARKER = f"idem-test-{uuid.uuid4().hex[:8]}"
client = TestClient(app)


@pytest.fixture(autouse=True)
def cleanup():
    yield
    cleanup_cases_by_marker(MARKER)
    r = get_redis()
    for key in r.scan_iter("refund:approval:*"):
        r.delete(key)
    # 清理幂等记录（审批/退款/测试键），避免跨测试命中旧结果
    db = SessionLocal()
    try:
        db.query(IdempotencyRecord).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def login() -> str:
    r = client.post("/api/v1/auth/login", json={"username": "manager", "password": "manager123"})
    return r.json()["access_token"]


def make_suspended_case() -> int:
    """创建 350 元案件并通过工作流真实挂起。"""
    r = client.post(
        "/api/v1/cases",
        data={
            "applicant_id": MARKER,
            "order_id": "order-idem",
            "applicant_amount": 35_000,
            "actual_amount": 35_000,
            "description": "挂起测试",
        },
        headers={"Authorization": f"Bearer {login()}"},
    )
    assert r.status_code == 202
    case_id = r.json()["case_id"]
    run_workflow(case_id, f"idem-suspend-{uuid.uuid4().hex}")
    return case_id


class TestDistributedLock:
    def test_second_acquire_fails(self):
        lock1 = DistributedLock("refund:approval:999", 10_000)
        lock2 = DistributedLock("refund:approval:999", 10_000)
        assert lock1.acquire() is True
        assert lock2.acquire() is False
        lock1.release()
        assert lock2.acquire() is True
        lock2.release()

    def test_lock_expires_after_ttl(self):
        lock1 = DistributedLock("refund:approval:888", ttl_ms=200)
        assert lock1.acquire() is True
        time.sleep(0.4)  # TTL 过期
        lock2 = DistributedLock("refund:approval:888", ttl_ms=10_000)
        assert lock2.acquire() is True  # 崩溃场景：锁自动过期，不死锁
        lock2.release()

    def test_release_only_own_token(self):
        """Lua 校验：B 的释放不能误删 A 的锁。"""
        lock_a = DistributedLock("refund:approval:777", 10_000)
        assert lock_a.acquire() is True
        # B 尝试释放（token 不匹配，Lua 返回 0，A 的锁仍在）
        get_redis().eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
            1, "refund:approval:777", "wrong-token",
        )
        assert get_redis().get("refund:approval:777") == lock_a.token
        lock_a.release()


class TestApprovalIdempotency:
    def test_same_idempotency_key_returns_first_result(self):
        """同幂等键重复请求：不重复执行审批，返回首次结果。"""
        case_id = make_suspended_case()
        headers = {
            "Authorization": f"Bearer {login()}",
            "X-Idempotency-Key": "approve-1",
        }
        r1 = client.post(
            f"/api/v1/cases/{case_id}/decision",
            json={"action": "APPROVE", "comment": "第一次"},
            headers=headers,
        )
        assert r1.status_code == 200
        r2 = client.post(
            f"/api/v1/cases/{case_id}/decision",
            json={"action": "APPROVE", "comment": "重复提交"},
            headers=headers,
        )
        assert r2.status_code == 200
        assert r2.json()["status"] == r1.json()["status"]
        # 只产生一笔退款流水
        db = SessionLocal()
        try:
            assert db.query(RefundRecord).filter_by(case_id=case_id).count() == 1
        finally:
            db.close()

    def test_concurrent_approvals_only_one_succeeds(self):
        """两个主管并发审批同一工单：只有一个成功，另一个 409 或幂等命中。"""
        case_id = make_suspended_case()
        results: list[int] = []
        barrier = threading.Barrier(2)

        def approve(tag: str):
            barrier.wait()
            r = client.post(
                f"/api/v1/cases/{case_id}/decision",
                json={"action": "APPROVE", "comment": f"主管{tag}"},
                headers={"Authorization": f"Bearer {login()}", "X-Idempotency-Key": f"conc-{tag}"},
            )
            results.append(r.status_code)

        t1 = threading.Thread(target=approve, args=("A",))
        t2 = threading.Thread(target=approve, args=("B",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert sorted(results) == [200, 409], f"并发结果异常: {results}"
        # 案件终态唯一、退款流水唯一
        db = SessionLocal()
        try:
            case = db.get(RefundCase, case_id)
            assert case.status == CaseStatus.COMPLETED.value
            assert db.query(RefundRecord).filter_by(case_id=case_id).count() == 1
        finally:
            db.close()

    def test_approved_case_cannot_be_approved_again(self):
        """已终态案件再次审批 -> 409（同幂等键不同键均不可绕过状态校验）。"""
        case_id = make_suspended_case()
        ok = client.post(
            f"/api/v1/cases/{case_id}/decision",
            json={"action": "APPROVE"},
            headers={"Authorization": f"Bearer {login()}", "X-Idempotency-Key": "k1"},
        )
        assert ok.status_code == 200
        again = client.post(
            f"/api/v1/cases/{case_id}/decision",
            json={"action": "APPROVE"},
            headers={"Authorization": f"Bearer {login()}", "X-Idempotency-Key": "k2"},
        )
        assert again.status_code == 409
        assert again.json()["code"] == "INVALID_STATE"


class TestRefundDedup:
    def test_refund_execution_idempotent(self):
        """退款动作独立幂等键：重复执行只产生一笔流水。"""
        from app.infrastructure.refund import MockRefundProvider

        case_id = make_suspended_case()
        # 先审批通过 -> 自动退款（COMPLETED）
        client.post(
            f"/api/v1/cases/{case_id}/decision",
            json={"action": "APPROVE"},
            headers={"Authorization": f"Bearer {login()}", "X-Idempotency-Key": "r-1"},
        )
        refund_key = build_key("refund", str(case_id))
        # 模拟 Worker/图重试再次触发退款
        result = MockRefundProvider().execute(case_id=case_id, amount_cent=35_000, refund_key=refund_key)
        assert result.success is True  # 幂等命中返回首次结果

        db = SessionLocal()
        try:
            assert db.query(RefundRecord).filter_by(case_id=case_id).count() == 1
        finally:
            db.close()


class TestIdempotencyService:
    def test_execute_idempotent_returns_first(self):
        calls = []

        def do():
            calls.append(1)
            return {"ok": True, "n": len(calls)}

        key = build_key("test", uuid.uuid4().hex, "2", "3")
        req_hash = hash_request({"x": 1})
        first, result1 = execute_idempotent(key, req_hash, do)
        assert first is True and result1 == {"ok": True, "n": 1}
        second, result2 = execute_idempotent(key, req_hash, do)
        assert second is False and result2 == {"ok": True, "n": 1}
        assert len(calls) == 1  # 只执行一次
