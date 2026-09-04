"""人工审核任务接口：挂起工单列表（Least Active 派单在 Phase 5/7 接入）。"""
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.schemas import ReviewTaskOut
from app.core.database import get_db
from app.core.security import ROLE_MANAGER, ROLE_CSR, get_current_user, require_roles
from app.domain.models import ReviewTask, User

router = APIRouter(prefix="/api/v1/review-tasks", tags=["review"])


@router.get("", response_model=list[ReviewTaskOut])
def list_review_tasks(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles(ROLE_MANAGER, ROLE_CSR))],
    status: str = "PENDING",
):
    """查看人工任务列表。主管可见全部；客服仅可见派给自己的（Least Active 生效后）。"""
    q = db.query(ReviewTask).filter_by(status=status)
    if user.role != ROLE_MANAGER:
        q = q.filter(ReviewTask.assignee_id == user.username)
    return q.order_by(ReviewTask.created_at.desc()).limit(100).all()
