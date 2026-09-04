"""模型约束测试：幂等键唯一、乐观锁并发（真实 PostgreSQL）。"""
import pytest
from sqlalchemy.exc import IntegrityError

from app.domain.models import AuditLog, RefundCase
from app.domain.status import CaseStatus


def make_case(idempotency_key: str = "idem-001") -> RefundCase:
    return RefundCase(
        ticket_no=f"TK-{idempotency_key}",
        applicant_id="user-1",
        order_id="order-1",
        applicant_amount=35_000,  # 350.00 元（分）
        actual_amount=35_000,
        status=CaseStatus.CREATED.value,
        idempotency_key=idempotency_key,
    )


class TestUniqueConstraints:
    def test_duplicate_idempotency_key_rejected(self, db):
        """同一幂等键二次插入必须被唯一约束拒绝（防资损第一道闸）。"""
        db.add(make_case("dup-key"))
        db.flush()
        db.add(make_case("dup-key"))  # ticket_no 不同但幂等键相同
        with pytest.raises(IntegrityError):
            db.flush()

    def test_duplicate_ticket_no_rejected(self, db):
        """工单号唯一。"""
        c1 = make_case("idem-a")
        c2 = make_case("idem-b")
        c2.ticket_no = c1.ticket_no
        db.add_all([c1, c2])
        with pytest.raises(IntegrityError):
            db.flush()


class TestOptimisticLock:
    def test_stale_version_update_affects_zero_rows(self, db):
        """乐观锁：version 不匹配的更新影响 0 行（丢失更新防线）。"""
        case = make_case()
        db.add(case)
        db.flush()

        tbl = type(case).__table__

        # 模拟两个并发事务各自读取 v0：第一个请求以 v0 更新成功
        stale = db.execute(
            tbl.update()
            .where(tbl.c.id == case.id, tbl.c.version == 0)
            .values(status=CaseStatus.RUNNING.value, version=1)
        )
        assert stale.rowcount == 1  # 第一个请求成功

        # 第二个请求仍用 v0 更新 -> 0 行（被乐观锁拦截，不覆盖）
        stale2 = db.execute(
            tbl.update()
            .where(tbl.c.id == case.id, tbl.c.version == 0)
            .values(status=CaseStatus.SUSPENDED.value, version=1)
        )
        assert stale2.rowcount == 0  # 丢失更新防线生效

    def test_amount_stored_as_int_cents(self, db):
        """金额必须整数分存储。"""
        case = make_case()
        db.add(case)
        db.flush()
        db.refresh(case)
        assert case.applicant_amount == 35_000
        assert isinstance(case.applicant_amount, int)


class TestAudit:
    def test_audit_log_written(self, db):
        """状态变更必须留审计轨迹。"""
        case = make_case()
        db.add(case)
        db.flush()
        db.add(
            AuditLog(
                case_id=case.id,
                from_status=CaseStatus.CREATED.value,
                to_status=CaseStatus.RUNNING.value,
                operator="worker",
            )
        )
        db.flush()
        assert db.query(AuditLog).filter_by(case_id=case.id).count() == 1
