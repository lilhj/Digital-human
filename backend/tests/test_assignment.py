"""L-3 修复：Least Active 派单（裁决 D-011）——挂起转人工时分派负载最轻的在线客服。

- 单测 pick_least_active：PENDING 数最少者被选中；非在册/停用客服排除；空池返回 None；
- 集成：run_workflow 触发人工挂起后，ReviewTask.assignee_id 与 RefundCase.assignee_id
  同步落在与当前库态 Least Active 计算结果一致的客服上（不再永久为空）。
"""
import uuid

import pytest

from app.core.database import SessionLocal
from app.core.security import ROLE_CSR
from app.domain.models import RefundCase, ReviewTask, User
from app.domain.status import CaseStatus
from app.workflow.assignment import pick_least_active
from app.workflow.graph import run_workflow
from tests.conftest import cleanup_cases_by_marker

MARKER = f"assign-{uuid.uuid4().hex[:8]}"


def _make_user(username: str) -> User:
    return User(
        username=username,
        password_hash="x",
        role=ROLE_CSR,
        display_name="客服测试",
        is_active=True,
    )


def _make_case(amount_cent: int = 35_000) -> int:
    """在事务内建一个 CREATED 案件（超 300 阈值，必然转人工挂起）。"""
    db = SessionLocal()
    try:
        c = RefundCase(
            ticket_no=f"A{uuid.uuid4().hex[:10].upper()}",
            applicant_id=MARKER,
            order_id="order-wf",
            applicant_amount=amount_cent,
            actual_amount=amount_cent,
            description="商品破损，申请退款",
            status=CaseStatus.CREATED.value,
            idempotency_key=f"assign-{uuid.uuid4().hex}",
        )
        db.add(c)
        db.commit()
        return c.id
    finally:
        db.close()


def _session_case(db, idem_key: str) -> int:
    """在 db fixture 会话内新建一个 SUSPENDED 案件（flush 后返回 id）。"""
    c = RefundCase(
        ticket_no=f"A{uuid.uuid4().hex[:10].upper()}",
        applicant_id=MARKER,
        order_id="order-wf",
        applicant_amount=100,
        actual_amount=100,
        description="d",
        status=CaseStatus.SUSPENDED.value,
        idempotency_key=idem_key,
    )
    db.add(c)
    db.flush()
    return c.id


def _retire_all_csrs(db) -> None:
    """把存量（非本测试）客服在事务内停用，保证只用自建客服验证派单逻辑。

    仅作用于 db fixture 的事务连接，测试结束回滚，不污染开发库 seed 客服。
    """
    db.query(User).filter(User.role == ROLE_CSR, ~User.username.like(f"{MARKER}-%")).update(
        {User.is_active: False}, synchronize_session=False
    )


@pytest.fixture(autouse=True)
def cleanup():
    yield
    cleanup_cases_by_marker(MARKER)
    db = SessionLocal()
    try:
        db.query(User).filter(User.username.like(f"{MARKER}-%")).delete(
            synchronize_session=False
        )
        db.commit()
    finally:
        db.close()


class TestPickLeastActive:
    def test_picks_min_pending_load(self, db):
        """负载 1 vs 0 -> 选 0 的那位。"""
        _retire_all_csrs(db)
        u1, u2 = _make_user(f"{MARKER}-1"), _make_user(f"{MARKER}-2")
        db.add_all([u1, u2])
        c1 = RefundCase(
            ticket_no=f"A{uuid.uuid4().hex[:10].upper()}",
            applicant_id=MARKER,
            order_id="order-wf",
            applicant_amount=100,
            actual_amount=100,
            description="d",
            status=CaseStatus.SUSPENDED.value,
            idempotency_key=f"assign-{uuid.uuid4().hex}",
        )
        db.add(c1)
        db.flush()
        db.add(ReviewTask(case_id=c1.id, status="PENDING", assignee_id=u1.username, idempotency_key="k1"))
        db.flush()
        assert pick_least_active(db) == u2.username

    def test_tie_breaks_to_lower_id(self, db):
        """负载齐平（都是 1）时选中 id 较小的客服（稳定优先序）。

        一案一任务（review_tasks.case_id 唯一），故用两个案件各挂一单构造平手。
        """
        _retire_all_csrs(db)
        u1, u2 = _make_user(f"{MARKER}-1"), _make_user(f"{MARKER}-2")
        db.add_all([u1, u2])
        c1 = _session_case(db, "k1")
        c2 = _session_case(db, "k2")
        db.add(ReviewTask(case_id=c1, status="PENDING", assignee_id=u1.username, idempotency_key="k1"))
        db.add(ReviewTask(case_id=c2, status="PENDING", assignee_id=u2.username, idempotency_key="k2"))
        db.flush()
        assert pick_least_active(db) == u1.username

    def test_immutable_pending_only(self, db):
        """无 PENDING 记录 -> 所有客服负载记为 0，选中 id 最小者，而非统计 APPROVED。"""
        _retire_all_csrs(db)
        u1, u2 = _make_user(f"{MARKER}-1"), _make_user(f"{MARKER}-2")
        db.add_all([u1, u2])
        c1 = RefundCase(
            ticket_no=f"A{uuid.uuid4().hex[:10].upper()}",
            applicant_id=MARKER,
            order_id="order-wf",
            applicant_amount=100,
            actual_amount=100,
            description="d",
            status=CaseStatus.SUSPENDED.value,
            idempotency_key=f"assign-{uuid.uuid4().hex}",
        )
        db.add(c1)
        db.flush()
        # 已办结的审核任务不计入负载
        db.add(ReviewTask(case_id=c1.id, status="APPROVED", assignee_id=u1.username, idempotency_key="k1"))
        db.flush()
        assert pick_least_active(db) == u1.username

    def test_inactive_user_excluded(self, db):
        """停用客服无权被派单。"""
        _retire_all_csrs(db)
        inactive = User(
            username=f"{MARKER}-off",
            password_hash="x",
            role=ROLE_CSR,
            display_name="已离职",
            is_active=False,
        )
        active = _make_user(f"{MARKER}-on")
        db.add_all([inactive, active])
        db.flush()
        assert pick_least_active(db) == active.username

    def test_no_csr_returns_none(self, db):
        """客服池为空 -> None（任务留无主，主管兜底可见）。"""
        _retire_all_csrs(db)
        assert pick_least_active(db) is None


class TestReviewTaskAssignment:
    """端到端验证人工挂起落 assignee。运行期间把存量客服停用，
    确保负载只在本测试自建的客服之间再平衡（测后恢复 seed 原状）。"""

    _retired: list[str] = []

    @pytest.fixture(autouse=True)
    def _isolate_csrs(self):
        self.__class__._retired = []
        db = SessionLocal()
        try:
            for u in db.query(User).filter(
                User.role == ROLE_CSR, ~User.username.like(f"{MARKER}-%")
            ).all():
                if u.is_active:
                    u.is_active = False
                    self.__class__._retired.append(u.username)
            db.commit()
        finally:
            db.close()
        yield
        db = SessionLocal()
        try:
            if self.__class__._retired:
                db.query(User).filter(User.username.in_(self.__class__._retired)).update(
                    {User.is_active: True}, synchronize_session=False
                )
                db.commit()
        finally:
            db.close()

    def test_human_review_persists_assignee_and_case(self):
        """端到端：人工挂起后 ReviewTask + RefundCase 都分派到同一客服。"""
        db = SessionLocal()
        try:
            db.add_all([_make_user(f"{MARKER}-a"), _make_user(f"{MARKER}-b")])
            db.commit()
            expected = pick_least_active(db)
            assert expected is not None  # 池内确实有可用客服（测试自建）
            assert expected in (f"{MARKER}-a", f"{MARKER}-b")
        finally:
            db.close()

        case_id = _make_case()
        result = run_workflow(case_id, f"trace-{uuid.uuid4().hex}")
        assert "__interrupt__" in result

        db = SessionLocal()
        try:
            task = db.query(ReviewTask).filter_by(case_id=case_id).first()
            assert task is not None
            assert task.status == "PENDING"
            assert task.assignee_id == expected
            case = db.get(RefundCase, case_id)
            assert case.assignee_id == task.assignee_id
        finally:
            db.close()

    def test_assignment_rebalances_after_adoption(self):
        """甲已被挂起任务分流后，新挂起案件派给负载更轻的乙（Least Active 生效）。"""
        db = SessionLocal()
        try:
            db.add_all([_make_user(f"{MARKER}-a"), _make_user(f"{MARKER}-b")])
            db.commit()
        finally:
            db.close()

        # 甲先接一单：把 1 个已挂起案件的 ReviewTask 派给甲
        first = _make_case()
        db = SessionLocal()
        try:
            db.query(ReviewTask).filter_by(case_id=first).delete(synchronize_session=False)
            db.add(ReviewTask(case_id=first, status="PENDING", assignee_id=f"{MARKER}-a", idempotency_key="adopt-1"))
            db.commit()
        finally:
            db.close()

        second = _make_case()
        result = run_workflow(second, f"trace-{uuid.uuid4().hex}")
        assert "__interrupt__" in result

        db = SessionLocal()
        try:
            task = db.query(ReviewTask).filter_by(case_id=second).first()
            # 甲已负载 1，乙负载 0 -> 新任务滚给乙
            assert task.assignee_id == f"{MARKER}-b"
            case = db.get(RefundCase, second)
            assert case.assignee_id == f"{MARKER}-b"
        finally:
            db.close()