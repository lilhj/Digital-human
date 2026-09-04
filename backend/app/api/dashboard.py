"""大屏概览接口：挂起数、决策量、风险分布（规格 06 第 13 节）。"""
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.domain.models import RefundCase
from app.domain.status import CaseStatus
from app.workflow.nodes import decision_policy

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


@router.get("/overview")
def overview(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[dict, Depends(get_current_user)],
):
    """概览统计：今日决策量、挂起数、各状态分布、风险等级分布。"""
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    total = db.query(func.count(RefundCase.id)).scalar() or 0
    suspended = (
        db.query(func.count(RefundCase.id))
        .filter(RefundCase.status == CaseStatus.SUSPENDED.value)
        .scalar()
        or 0
    )
    today_created = (
        db.query(func.count(RefundCase.id))
        .filter(RefundCase.created_at >= today_start)
        .scalar()
        or 0
    )
    today_completed = (
        db.query(func.count(RefundCase.id))
        .filter(
            RefundCase.status == CaseStatus.COMPLETED.value,
            RefundCase.updated_at >= today_start,
        )
        .scalar()
        or 0
    )

    # 风险分布（风险分三段）
    risk_low = db.query(func.count(RefundCase.id)).filter(
        RefundCase.risk_score <= decision_policy.risk_auto_max
    ).scalar() or 0
    risk_mid = db.query(func.count(RefundCase.id)).filter(
        RefundCase.risk_score > decision_policy.risk_auto_max,
        RefundCase.risk_score <= decision_policy.risk_review_max,
    ).scalar() or 0
    risk_high = db.query(func.count(RefundCase.id)).filter(
        RefundCase.risk_score > decision_policy.risk_review_max
    ).scalar() or 0

    # 各状态计数
    status_counts = {
        s.value: db.query(func.count(RefundCase.id))
        .filter(RefundCase.status == s.value)
        .scalar()
        or 0
        for s in CaseStatus
    }

    return {
        "total": total,
        "suspended": suspended,
        "today_created": today_created,
        "today_completed": today_completed,
        "risk_distribution": {"low": risk_low, "medium": risk_mid, "high": risk_high},
        "status_counts": status_counts,
    }
