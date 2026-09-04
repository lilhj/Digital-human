"""状态机测试：合法流转、非法流转、终态。"""
import pytest

from app.domain.status import (
    CaseStatus,
    IllegalStateTransitionError,
    TERMINAL_STATUSES,
    TRANSITIONS,
    assert_transition,
    can_transition,
)


class TestStateMachine:
    def test_happy_path_auto_refund(self):
        """自动退款主链路：CREATED -> RUNNING -> APPROVED -> REFUNDING -> COMPLETED。"""
        path = [
            CaseStatus.CREATED,
            CaseStatus.RUNNING,
            CaseStatus.APPROVED,
            CaseStatus.REFUNDING,
            CaseStatus.COMPLETED,
        ]
        for cur, nxt in zip(path, path[1:]):
            assert can_transition(cur, nxt), f"{cur} -> {nxt} 应合法"
            assert_transition(cur, nxt)  # 不抛错

    def test_happy_path_human_review(self):
        """人工审批链路：SUSPENDED -> APPROVED / REJECTED。"""
        assert can_transition(CaseStatus.SUSPENDED, CaseStatus.APPROVED)
        assert can_transition(CaseStatus.SUSPENDED, CaseStatus.REJECTED)

    def test_illegal_transition_raises(self):
        """非法转换必须抛错，不静默覆盖。"""
        illegal_pairs = [
            (CaseStatus.CREATED, CaseStatus.COMPLETED),  # 跳过 RUNNING
            (CaseStatus.SUSPENDED, CaseStatus.RUNNING),  # 挂起不可回退 RUNNING
            (CaseStatus.APPROVED, CaseStatus.REJECTED),  # 已批准不可反悔
            (CaseStatus.COMPLETED, CaseStatus.REFUNDING),  # 终态不可流转
            (CaseStatus.FAILED, CaseStatus.RUNNING),
        ]
        for cur, nxt in illegal_pairs:
            assert not can_transition(cur, nxt)
            with pytest.raises(IllegalStateTransitionError):
                assert_transition(cur, nxt)

    def test_terminal_statuses_are_final(self):
        """终态无任何出路。"""
        for s in TERMINAL_STATUSES:
            assert TRANSITIONS[s] == set()

    def test_all_statuses_covered(self):
        """所有状态在转换表中有定义且为合法枚举值。"""
        assert set(TRANSITIONS.keys()) == set(CaseStatus)
