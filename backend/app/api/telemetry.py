"""系统监控接口（telemetry 可观测聚合）。

- GET /api/v1/telemetry/summary
  Langfuse 上报管道状态（enabled/keys/SDK 安装）+ spool 队列（pending/failed/最近条目）
  + 节点真实时延（AgentRun 聚合：avg/p95）+ 端到端时延 + DLQ 死信（Redis）
  + 工单8 Token 优化红线（离线基准）。

诚实原则：Token 成本未采集（LLM 为 Fake/规则），不输出假数字；上报失败明细
直接来自 spool SQLite，让「为什么 Langfuse 页面没数据」在本页可自答。
"""
import base64
import importlib.util
import json
import sqlite3
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.domain.models import AgentRun, RefundCase, User

router = APIRouter(prefix="/api/v1/telemetry", tags=["telemetry"])

_NODE_LABEL = {
    "INTAKE": "Intake 接入",
    "INTENT": "意图识别",
    "ORDER_VERIFY": "订单三查",
    "EVIDENCE": "Evidence OCR",
    "FRAUD": "Fraud+Sentiment 合并",
    "SENTIMENT": "Sentiment 舆情",
    "DECISION": "Decision 决策",
    "HUMAN_REVIEW": "人工审批",
    "FINALIZE": "Finalize 终态",
    "CRITIC": "Critic 注入检测",
    "DLP": "DLP 脱敏",
}


def _spool_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "var" / "spool"


def _spool_stats() -> dict:
    """spool SQLite 统计 + 最近条目。DB 不存在/为空时返回零值（零降级路径）。"""
    db_path = _spool_dir() / "trace_spool.db"
    if not db_path.exists():
        return {"total": 0, "pending": 0, "failed": 0, "recent": []}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        total = conn.execute("SELECT COUNT(*) n FROM trace_spool").fetchone()["n"]
        by_status = {
            r["status"]: r["n"]
            for r in conn.execute("SELECT status, COUNT(*) n FROM trace_spool GROUP BY status")
        }
        recent = []
        for r in conn.execute(
            "SELECT * FROM trace_spool ORDER BY created_at DESC LIMIT 10"
        ):
            payload = json.loads(r["trace_json"])
            recent.append({
                "time": datetime.fromtimestamp(r["created_at"]).strftime("%m-%d %H:%M:%S"),
                "node": payload.get("node", "?"),
                "case_id": payload.get("case_id"),
                "duration_ms": payload.get("duration_ms"),
                "status": r["status"],
                "attempts": r["attempts"],
            })
        return {
            "total": total,
            "pending": by_status.get("pending", 0),
            "failed": by_status.get("failed", 0),
            "recent": recent,
        }
    finally:
        conn.close()


def _p95(values: list[int]) -> int:
    if not values:
        return 0
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * 0.95))]


def _node_latency(db: Session) -> tuple[list[dict], dict]:
    """AgentRun 聚合：各节点 count/avg/p95 + 端到端（按 case 求和）。"""
    rows = db.query(AgentRun.agent_name, AgentRun.duration_ms, AgentRun.case_id).all()
    by_node: dict[str, list[int]] = {}
    by_case: dict[int, list[int]] = {}
    for name, ms, case_id in rows:
        if ms is None:
            continue
        by_node.setdefault(name, []).append(ms)
        by_case.setdefault(case_id, []).append(ms)

    node_stats = [
        {
            "node": name,
            "label": _NODE_LABEL.get(name, name),
            "count": len(ms),
            "avg_ms": round(sum(ms) / len(ms)),
            "p95_ms": _p95(ms),
        }
        for name, ms in sorted(by_node.items(), key=lambda kv: -sum(kv[1]) / len(kv[1]))
    ]
    all_ms = [ms for ms in by_case.values() for ms in ms]
    e2e = {
        "count": len(by_case),
        "avg_ms": round(sum(all_ms) / len(all_ms)) if all_ms else 0,
        "p95_ms": _p95(all_ms),
    }
    return node_stats, e2e


@lru_cache(maxsize=1)
def _token_redline() -> dict:
    """工单8 Token 优化红线（离线确定性基准，进程内缓存）。"""
    from app.eval.intent_benchmark import run_intent_benchmark

    res = run_intent_benchmark()
    return {
        "token_reduction": res["TokenReduction"],
        "rule_hit_rate": res["rule_hit_rate"],
    }


@router.get("/summary")
def telemetry_summary(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    from app.infrastructure import dead_letter

    s = get_settings()
    node_latency, e2e = _node_latency(db)

    dlq_items = []
    for it in dead_letter.drain(10):
        dlq_items.append({
            "time": datetime.fromtimestamp(it.get("ts", time.time())).strftime("%m-%d %H:%M:%S"),
            "stage": it.get("stage"),
            "case_id": it.get("case_id"),
            "error": str(it.get("error", ""))[:200],
        })

    return {
        "langfuse": {
            "enabled": s.langfuse_enabled,
            "keys_configured": bool(s.langfuse_secret_key and s.langfuse_public_key),
            "module_installed": importlib.util.find_spec("langfuse") is not None,
            "base_url": s.langfuse_base_url,
        },
        "spool": _spool_stats(),
        "node_latency": node_latency,
        "end_to_end": e2e,
        "dlq": {"length": dead_letter.length(), "items": dlq_items},
        "optimization": _token_redline(),
    }


@router.get("/langfuse/traces")
def langfuse_traces(
    user: Annotated[User, Depends(get_current_user)],
    limit: int = 10,
):
    """直读 Langfuse 云端 public API 的真实 Trace 数据（Basic pk:sk 鉴权）。

    展示用：只取 trace 元数据与观测（id/名称/时延/case_id/节点），
    不回传 input/output 内容（避免泄露案件描述）。
    云端不可达/密钥缺失 -> ok:false + error（诚实降级，不造假）。
    """
    s = get_settings()
    if not (s.langfuse_secret_key and s.langfuse_public_key):
        return {
            "ok": False,
            "error": "Langfuse 密钥未配置，无法读取云端数据",
            "total_traces": None,
            "total_observations": None,
            "traces": [],
        }
    auth = "Basic " + base64.b64encode(
        f"{s.langfuse_public_key}:{s.langfuse_secret_key}".encode()
    ).decode()
    try:
        with httpx.Client(timeout=30) as c:
            traces_resp = c.get(
                f"{s.langfuse_base_url}/api/public/traces",
                params={"page": 1, "limit": limit},
                headers={"Authorization": auth},
            )
            traces_resp.raise_for_status()
            traces_data = traces_resp.json()
            obs_resp = c.get(
                f"{s.langfuse_base_url}/api/public/observations",
                params={"page": 1, "limit": 1},
                headers={"Authorization": auth},
            )
            obs_resp.raise_for_status()
            obs_data = obs_resp.json()
    except (httpx.HTTPError, ValueError) as e:
        return {
            "ok": False,
            "error": f"Langfuse 云端读取失败: {e}",
            "total_traces": None,
            "total_observations": None,
            "traces": [],
        }

    traces = []
    for t in traces_data.get("data", []):
        meta = t.get("metadata") or {}
        obs = [o for o in (t.get("observations") or []) if isinstance(o, dict)]
        traces.append(
            {
                "id": t.get("id"),
                "name": t.get("name"),
                "timestamp": t.get("timestamp"),
                "case_id": meta.get("case_id"),
                "node": meta.get("node"),
                "duration_ms": meta.get("duration_ms"),
                "status": meta.get("status"),
                "obs_count": len(obs),
                "observations": [
                    {
                        "id": o.get("id"),
                        "type": o.get("type"),
                        "name": o.get("name"),
                        "latency_ms": o.get("latency"),
                    }
                    for o in obs
                ],
            }
        )
    return {
        "ok": True,
        "error": None,
        "total_traces": (traces_data.get("meta") or {}).get("totalItems"),
        "total_observations": (obs_data.get("meta") or {}).get("totalItems"),
        "traces": traces,
    }
