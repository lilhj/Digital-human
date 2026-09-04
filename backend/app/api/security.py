"""安全中心汇总接口（工单6 三道闸运行期可观测）。

- GET  /api/v1/security/summary?range=7d   Critic 注入拦截 / DLP 脱敏命中 /
  Tool 白名单拦截 统计 + 最近安全事件流水 + 沙箱运行模式。
- GET  /api/v1/security/red-blue           读取最近一次红蓝对抗报告（拦截率/越狱/漏报/误报 + 红线）
- POST /api/v1/security/red-blue/run       一键触发红蓝对抗压测（MANAGER 限定）

数据源（summary）：AgentRun 轨迹（CRITIC / DLP / FINALIZE）。量级 = 案件数，Python 侧聚合足够；
不引入 JSONB SQL 方言查询，保持与 intent/summary 相同的轻量风格。
"""
from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import ROLE_MANAGER, get_current_user, require_roles
from app.domain.models import AgentRun, RefundCase, User
from app.security import red_blue

router = APIRouter(prefix="/api/v1/security", tags=["security"])

_LEVEL_EVENT_TYPE = {"CRITIC": "Critic 注入拦截", "DLP": "DLP 脱敏", "TOOL": "Tool 白名单过滤"}


def _cutoff_of(range: str) -> datetime | None:
    if range == "today":
        return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if range == "7d":
        return datetime.now() - timedelta(days=7)
    if range == "30d":
        return datetime.now() - timedelta(days=30)
    if range != "all":
        raise HTTPException(status_code=400, detail={"code": "BAD_RANGE", "message": "range 仅支持 7d/30d/today/all"})
    return None


@router.get("/summary")
def security_summary(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    range: str = "7d",
):
    cutoff = _cutoff_of(range)

    q = db.query(AgentRun).filter(AgentRun.agent_name.in_(["CRITIC", "DLP", "FINALIZE"]))
    if cutoff is not None:
        q = q.filter(AgentRun.started_at >= cutoff)
    rows = q.order_by(AgentRun.started_at.desc()).all()

    case_ids = {r.case_id for r in rows}
    tickets: dict[int, str] = {}
    if case_ids:
        for c in db.query(RefundCase.id, RefundCase.ticket_no).filter(RefundCase.id.in_(case_ids)):
            tickets[c.id] = c.ticket_no

    critic_scanned = critic_blocked = inj = jail = 0
    dlp_scanned = dlp_hits = masked_total = 0
    tool_blocked = 0
    events: list[dict[str, Any]] = []

    for r in rows:
        out = r.output_json or {}
        ticket = tickets.get(r.case_id, f"#{r.case_id}")
        time_str = (r.finished_at or r.started_at).strftime("%m-%d %H:%M:%S") if (r.finished_at or r.started_at) else "-"

        if r.agent_name == "CRITIC":
            critic_scanned += 1
            if out.get("is_injection"):
                inj += 1
            if out.get("is_jailbreak"):
                jail += 1
            if out.get("action") == "BLOCK":
                critic_blocked += 1
                matched = out.get("matched") or []
                events.append({
                    "time": time_str,
                    "type": _LEVEL_EVENT_TYPE["CRITIC"],
                    "case_id": r.case_id,
                    "ticket_no": ticket,
                    "detail": f"风险分 {float(out.get('risk_score') or 0):.2f}，命中 {len(matched)} 条注入模式，转人工复核",
                    "level": "高",
                })
        elif r.agent_name == "DLP":
            dlp_scanned += 1
            chars = int(out.get("masked_chars") or 0)
            masked_total += chars
            if out.get("pii_hit"):
                dlp_hits += 1
                events.append({
                    "time": time_str,
                    "type": _LEVEL_EVENT_TYPE["DLP"],
                    "case_id": r.case_id,
                    "ticket_no": ticket,
                    "detail": f"PII 已脱敏（遮掩 {chars} 字符），原文/密文均不入日志",
                    "level": "中",
                })
        else:  # FINALIZE：Tool 白名单过滤（退款执行前最后一道闸）
            if out.get("security_blocked"):
                tool_blocked += 1
                events.append({
                    "time": time_str,
                    "type": _LEVEL_EVENT_TYPE["TOOL"],
                    "case_id": r.case_id,
                    "ticket_no": ticket,
                    "detail": str(out.get("security_block_reason") or "越权退款指令被拦截"),
                    "level": "高",
                })

    events.sort(key=lambda e: e["time"], reverse=True)

    # 沙箱运行模式（无历史计数，只报当前开关状态；批量执行的隔离执行在 batch 模块）
    from app.core.config import get_settings
    from app.infrastructure.streams import get_sandbox_mode_runtime

    sandbox_mode = get_sandbox_mode_runtime() or get_settings().sandbox_mode

    return {
        "range": range,
        "critic": {
            "scanned": critic_scanned,
            "blocked": critic_blocked,
            "block_rate": round(critic_blocked / critic_scanned, 3) if critic_scanned else 0.0,
            "injection": inj,
            "jailbreak": jail,
        },
        "dlp": {
            "scanned": dlp_scanned,
            "pii_hits": dlp_hits,
            "hit_rate": round(dlp_hits / dlp_scanned, 3) if dlp_scanned else 0.0,
            "masked_chars_total": masked_total,
        },
        "tool_filter": {"blocked": tool_blocked},
        "sandbox": {"mode": sandbox_mode},
        "events": events[:30],
    }


# ---------------- 红蓝对抗（工单6 任务三，独立验收材料） ----------------

@router.get("/red-blue")
def get_red_blue_report(
    user: Annotated[User, Depends(get_current_user)],
):
    """读取最近一次红蓝对抗报告；未跑过 -> ok:false（前端据此引导先一键触发）。"""
    return red_blue.load_red_blue()


@router.post("/red-blue/run")
def run_red_blue_report(
    user: Annotated[User, Depends(require_roles(ROLE_MANAGER))],
):
    """一键触发红蓝对抗压测：100+ 变种注入样本（Base64/越狱/多语言）+ DLP 漏报/误报统计。

    离线确定性（规则引擎），秒级完成；落 JSON + MD 双报告（docs/red_blue_test_report.*）。
    """
    return red_blue.run_red_blue()
