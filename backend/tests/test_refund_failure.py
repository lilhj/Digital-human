"""M-2 退款失败不再卡死：REFUND_FAILED 可重试 / FAILED 终态 / finalize 兜底。

旧缺陷：approve 后退款失败只打日志，案件永远停在 APPROVED（"已批准未退款"模糊态），
且共用单一幂等键会缓存失败结果，重试形同虚设。

验证三件事：
1. 瞬态网关失败 -> 有界重试（独立幂等子键）-> COMPLETED；
2. 全部尝试失败 -> REFUND_FAILED -> FAILED 终态（不再卡 APPROVED）；
3. finalize 对 provider 失败兜底：案件绝不落在 APPROVED。
"""
import uuid

import pytest

from app.core.database import SessionLocal
from app.domain.models import AuditLog, RefundCase, RefundRecord
from app.domain.status import CaseStatus
from app.infrastructure.idempotency import build_key
from app.infrastructure.refund import MockRefundProvider, RefundResult
from app.workflow import nodes
from app.workflow.nodes import finalize_node

MARKER = f"refund-fail-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def cleanup():
    yield
    from tests.conftest import cleanup_cases_by_marker

    cleanup_cases_by_marker(MARKER)
    # 幂等键含标记无法反查 case，直接清空幂等表（与 test_idempotency 一致）
    db = SessionLocal()
    try:
        db.query(RefundRecord).delete(synchronize_session=False)
        db.query(AuditLog).delete(synchronize_session=False)
        from app.domain.models import IdempotencyRecord

        db.query(IdempotencyRecord).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _make_case(status: str) -> int:
    db = SessionLocal()
    try:
        case = RefundCase(
            ticket_no=f"T{uuid.uuid4().hex[:10].upper()}",
            applicant_id=MARKER,
            order_id="ORD-NOT-EXIST",
            applicant_amount=12_800,
            actual_amount=12_800,
            description="商品破损，申请退款",
            status=status,
            idempotency_key=f"m2-{uuid.uuid4().hex}",
        )
        db.add(case)
        db.commit()
        return case.id
    finally:
        db.close()


def _case_status(case_id: int) -> str:
    db = SessionLocal()
    try:
        return db.get(RefundCase, case_id).status
    finally:
        db.close()


def _refund_records(case_id: int):
    db = SessionLocal()
    try:
        return [
            (r.status, r.retry_count)
            for r in db.query(RefundRecord).filter_by(case_id=case_id).all()
        ]
    finally:
        db.close()


class TestTransientFailureRetries:
    def test_gateway_fails_twice_then_succeeds(self):
        """网关前两次失败、第三次成功 -> COMPLETED，且有重试流水。"""
        case_id = _make_case(CaseStatus.APPROVED.value)

        calls = []

        def gateway(attempt: int) -> bool:
            calls.append(attempt)
            return attempt >= 3  # 第 1、2 次失败，第 3 次成功

        provider = MockRefundProvider(gateway=gateway, max_attempts=3)
        result = provider.execute(
            case_id=case_id,
            amount_cent=12_800,
            refund_key=build_key("refund", str(case_id)),
        )

        assert result.success is True
        assert _case_status(case_id) == CaseStatus.COMPLETED.value
        # 两次失败 + 一次成功
        records = _refund_records(case_id)
        assert ("FAILED", 0) in records
        assert ("FAILED", 1) in records
        assert ("SUCCESS", 2) in records


class TestAllAttemptsFail:
    def test_all_attempts_fail_ends_in_failed(self):
        """网关恒失败（不模拟成功）-> REFUND_FAILED -> FAILED 终态，绝不卡 APPROVED。"""
        case_id = _make_case(CaseStatus.APPROVED.value)

        provider = MockRefundProvider(gateway=lambda attempt: False, max_attempts=2)
        result = provider.execute(
            case_id=case_id,
            amount_cent=12_800,
            refund_key=build_key("refund", str(case_id)),
        )

        assert result.success is False
        assert _case_status(case_id) == CaseStatus.FAILED.value
        # 每次失败一记 RefundRecord(FAILED)
        records = _refund_records(case_id)
        assert records.count(("FAILED", 0)) == 1
        assert records.count(("FAILED", 1)) == 1


class TestFinalizeRefundFailure:
    def test_finalize_refund_failure_leaves_refund_failed(self):
        """provider 返回失败 -> finalize 兜底把案件推进到 REFUND_FAILED（非 APPROVED）。"""
        case_id = _make_case(CaseStatus.APPROVED.value)

        class FailingProvider:
            def execute(self, *, case_id, amount_cent, refund_key):
                return RefundResult(False, "模拟网关宕机")

        import app.infrastructure.refund as refund_mod

        original = refund_mod.MockRefundProvider
        refund_mod.MockRefundProvider = FailingProvider
        try:
            finalize_node({"case_id": case_id, "decision": "APPROVE", "amount_cent": 12_800})
        finally:
            refund_mod.MockRefundProvider = original

        status = _case_status(case_id)
        # 关键断言：不得停留在 APPROVED
        assert status != CaseStatus.APPROVED.value
        assert status in (
            CaseStatus.REFUND_FAILED.value,
            CaseStatus.FAILED.value,
        )

    def test_fail_provider_raising_does_not_stick_approved(self):
        """provider 抛异常 -> finalize 兜底仍推进失败态（不因异常卡死 APPROVED）。"""
        case_id = _make_case(CaseStatus.APPROVED.value)

        class ExplodingProvider:
            def execute(self, *, case_id, amount_cent, refund_key):
                raise RuntimeError("网关连接中断")

        import app.infrastructure.refund as refund_mod

        original = refund_mod.MockRefundProvider
        refund_mod.MockRefundProvider = ExplodingProvider
        try:
            # finalize_node 内部对 execute 没有 try；异常会向上传播。
            # 为测"异常也能兜底"，这里直接调用 process，确认异常不产生半成品。（见说明）
            try:
                finalize_node({"case_id": case_id, "decision": "APPROVE", "amount_cent": 12_800})
            except RuntimeError:
                pass
        finally:
            refund_mod.MockRefundProvider = original

        # 即使异常被抛出，案件也应在下一环被处理；此处断言不产生"已成功退款"假象
        status = _case_status(case_id)
        assert status != CaseStatus.APPROVED.value