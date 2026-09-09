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

# 进行中的案件状态（资损防护：防"一个订单退两次款"）。
# 同订单存在任一这些状态的案件时，禁止再开新单 / 再放行退款；
# REJECTED/COMPLETED/FAILED 为终态或已结案，不阻塞重新发起。
IN_FLIGHT_STATUSES = frozenset({
    CaseStatus.CREATED,
    CaseStatus.RUNNING,
    CaseStatus.SUSPENDED,
    CaseStatus.APPROVED,
    CaseStatus.REFUNDING,
    CaseStatus.REFUND_FAILED,
})

# 决策流三态（宏观）→ 案件八态（微观）映射：与前端 src/api/caseStage.ts 口径一致。
# 运行中：流水线在跑（含退款重试中）；挂起中：等人工审批；已完成：终态（含已拒单/流程终止）。
STAGE_STATUSES: dict[str, set[CaseStatus]] = {
    "RUNNING": {
        CaseStatus.CREATED,
        CaseStatus.RUNNING,
        CaseStatus.APPROVED,
        CaseStatus.REFUNDING,
        CaseStatus.REFUND_FAILED,
    },
    "SUSPENDED": {CaseStatus.SUSPENDED},
    "COMPLETED": {CaseStatus.COMPLETED, CaseStatus.REJECTED, CaseStatus.FAILED},
}


def statuses_in_stage(stage: str) -> set[str] | None:
    """三态阶段 → 命中的案件状态集合；未知三态返回 None（调用方应报 422）。"""
    statuses = STAGE_STATUSES.get(stage)
    return {s.value for s in statuses} if statuses else None


class IllegalStateTransitionError(ValueError):
    """非法状态转换：调用方应捕获并记录审计，不静默覆盖。"""


def can_transition(current: CaseStatus, target: CaseStatus) -> bool:
    return target in TRANSITIONS[current]


def assert_transition(current: CaseStatus, target: CaseStatus) -> None:
    if not can_transition(current, target):
        raise IllegalStateTransitionError(
            f"非法状态转换: {current.value} -> {target.value}"
        )
