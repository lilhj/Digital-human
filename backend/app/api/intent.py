"""工单8 意图识别大屏接口：运行期聚合 + 红线评测指标。

- GET /api/v1/intent/summary?range=7d  意图识别运行期统计（分布/路由/明细）
  + 评测红线（召回率/幻觉率/Token 降幅，来自离线基准，进程内缓存）。

range: 7d（近 7 天）/ 30d / today / all。仅统计已落库意图的案件（旧案件 intent 为 NULL 不计）。
"""
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.domain.models import RefundCase, User

router = APIRouter(prefix="/api/v1/intent", tags=["intent"])

# 意图/识别方式 → 中文标签 + 配色（对齐前端大屏）
_INTENT_LABEL = {"REFUND": "退款", "RETURN": "退货", "EXCHANGE": "换货", "UNKNOWN": "兜底转人工"}
_INTENT_COLOR = {"REFUND": "#0d6efd", "RETURN": "#6f42c1", "EXCHANGE": "#20c997", "UNKNOWN": "#868e96"}
_SOURCE_LABEL = {"rule": "规则", "llm": "LLM", "fallback": "兜底", "fake": "测试", "declared": "用户选择"}


def _route_of(intent: str | None, fallback: bool) -> str:
    if intent == "EXCHANGE":
        return "转人工（客服）"
    if intent == "UNKNOWN" or fallback:
        return "转人工兜底"
    return "决策流"


@lru_cache(maxsize=1)
def _latest_eval() -> dict:
    """红线评测指标：离线基准（确定性、零网络），进程内缓存一次。"""
    from app.eval.intent_benchmark import run_intent_benchmark

    res = run_intent_benchmark()
    return {
        "recall": res["Recall"],
        "hallucination_rate": res["HallucinationRate"],
        "token_reduction": res["TokenReduction"],
        "rule_hit_rate": res["rule_hit_rate"],
        "samples": res["samples"],
    }


@router.get("/summary")
def intent_summary(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    range: str = "7d",
):
    """意图识别运行期聚合（分布/路由/明细）+ 红线评测指标。"""
    cutoff = None
    if range == "today":
        cutoff = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    elif range == "7d":
        cutoff = datetime.now() - timedelta(days=7)
    elif range == "30d":
        cutoff = datetime.now() - timedelta(days=30)
    elif range != "all":
        raise HTTPException(status_code=400, detail={"code": "BAD_RANGE", "message": "range 仅支持 7d/30d/today/all"})

    q = db.query(RefundCase).filter(RefundCase.intent.isnot(None))
    if cutoff is not None:
        q = q.filter(RefundCase.created_at >= cutoff)

    total_identified = q.count()

    # 按意图分布
    dist_rows = (
        db.query(RefundCase.intent, func.count(RefundCase.id))
        .filter(RefundCase.intent.isnot(None))
    )
    if cutoff is not None:
        dist_rows = dist_rows.filter(RefundCase.created_at >= cutoff)
    dist_rows = dist_rows.group_by(RefundCase.intent).all()

    distribution = [
        {
            "intent": intent,
            "label": _INTENT_LABEL.get(intent, intent or "未知"),
            "count": cnt,
            "color": _INTENT_COLOR.get(intent, "#868e96"),
        }
        for intent, cnt in dist_rows
    ]

    # 按识别方式分布
    src_rows = (
        db.query(RefundCase.intent_source, func.count(RefundCase.id))
        .filter(RefundCase.intent_source.isnot(None))
    )
    if cutoff is not None:
        src_rows = src_rows.filter(RefundCase.created_at >= cutoff)
    src_rows = src_rows.group_by(RefundCase.intent_source).all()
    source_breakdown = {src: cnt for src, cnt in src_rows}

    # 转人工兜底数（EXCHANGE + UNKNOWN）
    to_human = (
        db.query(func.count(RefundCase.id))
        .filter(RefundCase.intent_fallback.is_(True))
    )
    if cutoff is not None:
        to_human = to_human.filter(RefundCase.created_at >= cutoff)
    to_human = to_human.scalar() or 0

    # 路由表
    dist_map = {d["intent"]: d["count"] for d in distribution}
    exchange_human = dist_map.get("EXCHANGE", 0)
    fallback_human = max(to_human - exchange_human, 0)
    routes = [
        {"route": "退款 → 自动决策链路", "count": dist_map.get("REFUND", 0), "note": "Evidence → 风控舆情 → Decision"},
        {"route": "退货 → 自动决策链路", "count": dist_map.get("RETURN", 0), "note": "同上，走金额决策"},
        {"route": "换货 → 转人工（客服）", "count": exchange_human, "note": "不涉及金额，MVP 不走决策流"},
        {"route": "低置信 → 转人工兜底", "count": fallback_human, "note": "JSON 损坏/识别不明保守路由"},
    ]

    # 明细（最近 30 条）
    detail_rows = (
        q.order_by(RefundCase.created_at.desc()).limit(30).all()
    )
    details = [
        {
            "time": c.created_at.strftime("%m-%d %H:%M"),
            "text": (c.description or "")[:60],
            "intent": _INTENT_LABEL.get(c.intent, c.intent or "未知"),
            "method": _SOURCE_LABEL.get(c.intent_source, c.intent_source or "-"),
            "conf": round(float(c.intent_confidence), 2) if c.intent_confidence is not None else 0.0,
            "route": _route_of(c.intent, bool(c.intent_fallback)),
        }
        for c in detail_rows
    ]

    return {
        "range": range,
        "total_identified": total_identified,
        "distribution": distribution,
        "to_human": to_human,
        "source_breakdown": source_breakdown,
        "routes": routes,
        "details": details,
        "eval": _latest_eval(),
    }
