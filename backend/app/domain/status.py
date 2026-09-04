"""退款案件状态机（规格 docs/06 第 6 节，八态）。

合法流转：
    CREATED -> RUNNING
    RUNNING -> SUSPENDED | APPROVED | REJECTED | FAILED
    SUSPENDED -> APPROVED | REJECTED
    APPROVED -> REFUNDING
    REFUNDING -> COMPLETED | REFUND_FAILED
    REFUND_FAILED -> REFUNDING（重试）| FAILED（重试耗尽）
    REJECTED -> COMPLETED（关闭工单）
    COMPLETED / FAILED 为终态
非法转换必须显式抛错，禁止静默覆盖状态。
"""
from enum import StrEnum


class CaseStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    SUSPENDED = "SUSPENDED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REFUNDING = "REFUNDING"
    REFUND_FAILED = "REFUND_FAILED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# 合法转换表
TRANSITIONS: dict[CaseStatus, set[CaseStatus]] = {
    CaseStatus.CREATED: {CaseStatus.RUNNING},
    CaseStatus.RUNNING: {
        CaseStatus.SUSPENDED,
        CaseStatus.APPROVED,
        CaseStatus.REJECTED,
        CaseStatus.FAILED,
    },
    CaseStatus.SUSPENDED: {CaseStatus.APPROVED, CaseStatus.REJECTED},
    CaseStatus.APPROVED: {CaseStatus.REFUNDING},
    CaseStatus.REFUNDING: {CaseStatus.COMPLETED, CaseStatus.REFUND_FAILED},
    CaseStatus.REFUND_FAILED: {CaseStatus.REFUNDING, CaseStatus.FAILED},
    CaseStatus.REJECTED: {CaseStatus.COMPLETED},
    CaseStatus.COMPLETED: set(),
    CaseStatus.FAILED: set(),
}

TERMINAL_STATUSES = {CaseStatus.COMPLETED, CaseStatus.FAILED}


class IllegalStateTransitionError(ValueError):
    """非法状态转换：调用方应捕获并记录审计，不静默覆盖。"""


def can_transition(current: CaseStatus, target: CaseStatus) -> bool:
    return target in TRANSITIONS[current]


def assert_transition(current: CaseStatus, target: CaseStatus) -> None:
    if not can_transition(current, target):
        raise IllegalStateTransitionError(
            f"非法状态转换: {current.value} -> {target.value}"
        )
