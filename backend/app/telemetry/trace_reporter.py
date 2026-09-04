"""Langfuse Trace 上报（工单5 移植）：先写本地 spool，再异步批量上报云端。绝不阻塞主事件循环。

对工单1 的适配：
- 打点粒度：每个 LangGraph 节点（INTAKE/EVIDENCE/FRAUD/SENTIMENT/DECISION/HUMAN_REVIEW/FINALIZE）
  作为一条 agent 观测上报；trace 维度 = case_id + 一次工作流。
- 异步非阻塞：spool 落盘（SQLite）+ 同步 client 的 flush 丢线程池，失败进指数退避不冒泡。
- LANGFUSE_ENABLED=false 时直接跳过（离线/验收降级路径）。
"""
import asyncio
import json
import logging
import threading
import uuid
from pathlib import Path

from app.core.config import get_settings
from app.telemetry.spool import SpoolStore

logger = logging.getLogger(__name__)
_RETRY_DELTAS = (1.0, 5.0, 30.0)


def workflow_trace_id() -> str:
    """Langfuse v4 要求 trace id 为 32 位小写 hex（完整 UUID）。"""
    return uuid.uuid4().hex


def _sanitize_trace_id(trace_id: str | None) -> str:
    """Langfuse v4 严格校验 32 位小写 hex：非法值降级为合法 UUID，绝不污染上报链路。

    工作流 trace_id 来自 Redis 消息（uuid4().hex 合法）；但手动/测试路径可能传入
    任意字符串（如 'smoke-trace'），SDK 校验失败会抛错并触发退避。此处统一兜底。
    """
    if (
        isinstance(trace_id, str)
        and len(trace_id) == 32
        and all(c in "0123456789abcdef" for c in trace_id)
    ):
        return trace_id
    return uuid.uuid4().hex


class TraceReporter:
    def __init__(self, spool_dir: Path | None = None, client_factory=None):
        s = get_settings()
        self.spool = SpoolStore(spool_dir or Path(__file__).resolve().parents[2] / "var" / "spool")
        self._factory = client_factory or _build_langfuse_client

    async def report_node(
        self,
        *,
        trace_id: str,
        case_id: int,
        node: str,
        duration_ms: int,
        metadata: dict | None = None,
    ) -> None:
        """上报一个节点的观测。失败走 spool 退避，绝不冒泡阻塞工作流。"""
        s = get_settings()
        if not s.langfuse_enabled:
            return
        trace_json = {
            "trace_id": trace_id,
            "case_id": case_id,
            "node": node,
            "duration_ms": duration_ms,
            "metadata": metadata or {},
        }
        try:
            self.spool.enqueue(trace_json)
            await self.flush()
        except Exception:  # noqa: BLE001 - 上报失败必须降级，异常不冒泡
            logger.warning("Trace 上报队列失败（降级）: case=%s node=%s", case_id, node)

    def report_node_sync(
        self,
        *,
        trace_id: str,
        case_id: int,
        node: str,
        duration_ms: int,
        metadata: dict | None = None,
    ) -> None:
        """同步上下文（LangGraph 节点）的降级上报：先写 spool（SQLite 快），
        再在后台线程驱动 flush（不阻塞工作流主路径）。"""
        s = get_settings()
        if not s.langfuse_enabled:
            return
        try:
            self.spool.enqueue(
                {
                    "trace_id": trace_id,
                    "case_id": case_id,
                    "node": node,
                    "duration_ms": duration_ms,
                    "metadata": metadata or {},
                }
            )
            threading.Thread(target=asyncio.run, args=(self.flush(),), daemon=True).start()
        except Exception:  # noqa: BLE001
            logger.warning("Trace 上报失败（降级）: case=%s node=%s", case_id, node)

    async def flush(self) -> None:
        rows = self.spool.pending()
        if not rows:
            return
        s = get_settings()
        if not s.langfuse_enabled:
            return
        client = None
        try:
            client = self._factory()
            from langfuse import types

            for row in rows:
                payload = json.loads(row["trace_json"])
                trace_id = payload.pop("trace_id")
                case_id = payload.pop("case_id", 0)
                obs = client.start_observation(
                    name=f"workflow-node-{payload.get('node', 'unknown')}",
                    as_type="agent",
                    trace_context=types.TraceContext(trace_id=trace_id),
                    input=payload.get("node"),
                    metadata={
                        "case_id": case_id,
                        "node": payload.get("node"),
                        "duration_ms": payload.get("duration_ms"),
                        **payload.get("metadata", {}),
                    },
                )
                obs.end()
            # flush 为同步阻塞方法，丢到线程池避免卡死事件循环
            await asyncio.to_thread(client.flush)
            self.spool.mark_success([r["id"] for r in rows])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Langfuse 上报失败，进入退避重试: %s", exc)
            groups: dict[float, list[int]] = {}
            for row in rows:
                delta = _RETRY_DELTAS[min(row["attempts"], 2)]
                groups.setdefault(delta, []).append(row["id"])
            for delta, ids in groups.items():
                self.spool.mark_backoff(ids, delta)


def _build_langfuse_client(cfg=None):
    s = get_settings()
    from langfuse import Langfuse

    return Langfuse(
        secret_key=s.langfuse_secret_key,
        public_key=s.langfuse_public_key,
        host=s.langfuse_base_url,
    )


# 全局单例（API/Worker 共用）
_reporter: TraceReporter | None = None


def get_reporter() -> TraceReporter:
    global _reporter
    if _reporter is None:
        _reporter = TraceReporter()
    return _reporter
