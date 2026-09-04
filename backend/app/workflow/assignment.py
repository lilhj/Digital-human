"""Least Active 派单（裁决 D-011）：把挂起的审核任务派给当前负载最轻的在线客服。

- 负载口径：该客服名下 PENDING 的 ReviewTask 数量（正在排队待处理的工作量）。
- 无在册且启用的客服时返回 None（MVP 兜底：任务保留 PENDING 无主，主管可全量可见）。
- 无 PENDING 时所有客服负载为 0，返回第一个（按 id 稳定排序）。
"""
from sqlalchemy.orm import Session

from app.core.security import ROLE_CSR
from app.domain.models import ReviewTask, User


def pick_least_active(db: Session) -> str | None:
    """返回负载最轻的在线客服用户名；无可用客服返回 None。"""
    csrs = (
        db.query(User)
        .filter(User.role == ROLE_CSR, User.is_active.is_(True))
        .order_by(User.id.asc())
        .all()
    )
    if not csrs:
        return None

    # 统计每个客服当前待处理（PENDING）任务数作为负载
    from sqlalchemy import func

    rows = (
        db.query(ReviewTask.assignee_id, func.count(ReviewTask.id))
        .filter(ReviewTask.status == "PENDING", ReviewTask.assignee_id.is_not(None))
        .group_by(ReviewTask.assignee_id)
        .all()
    )
    load: dict[str, int] = dict(rows)
    return min(csrs, key=lambda u: load.get(u.username, 0)).username