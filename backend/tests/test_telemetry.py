"""Telemetry 测试（工单5 移植）：SQLite spool 退避 + Langfuse 上报降级。

- spool enqueue/pending/success/backoff（退避增量 1s/5s/30s）
- MAX_ATTEMPTS 后置 failed
- LANGFUSE_ENABLED=false 降级跳过（不抛异常）
"""
import pytest
from pathlib import Path

from app.telemetry.spool import MAX_ATTEMPTS, SpoolStore
from app.telemetry.trace_reporter import TraceReporter, workflow_trace_id


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


@pytest.fixture
def spool(tmp_path):
    store = SpoolStore(tmp_path)
    clock = _Clock()
    store._clock = clock
    store._advance_clock = lambda s: clock.advance(s)
    return store


def test_spool_enqueue_pending(spool):
    spool.enqueue({"trace_id": "a" * 32, "node": "INTAKE"})
    rows = spool.pending()
    assert len(rows) == 1
    assert "trace_id" in rows[0]["trace_json"]


def test_spool_backoff_increments(spool):
    spool.enqueue({"trace_id": "b" * 32})
    ids = [r["id"] for r in spool.pending()]
    spool.mark_backoff(ids, 1.0)
    spool._advance_clock(0.5)
    assert spool.pending() == []  # 未到退避时间
    spool._advance_clock(1.0)
    rows = spool.pending()
    assert len(rows) == 1
    assert rows[0]["attempts"] == 1


def test_spool_max_attempts_failed(spool):
    spool.enqueue({"trace_id": "c" * 32})
    for _ in range(MAX_ATTEMPTS):
        rows = spool.pending()
        if not rows:
            break
        spool.mark_backoff([r["id"] for r in rows], 0.0)
    # 重试耗尽后不再返回（不丢数据，但标记 failed 终止重试）
    assert spool.pending() == []


def test_reporter_disabled_noop(tmp_path, monkeypatch):
    """LANGFUSE_ENABLED=false：上报直接跳过，不抛异常（离线降级）。"""
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    reporter = TraceReporter(spool_dir=tmp_path)
    # 不触发真实网络：client_factory 返回会炸的假客户端也应被禁用短路
    reporter._factory = lambda: (_ for _ in ()).throw(RuntimeError("不应被调用"))
    import asyncio

    asyncio.run(reporter.report_node(trace_id="d" * 32, case_id=1, node="INTAKE", duration_ms=5))


def test_workflow_trace_id_is_32_hex():
    tid = workflow_trace_id()
    assert len(tid) == 32
    assert all(c in "0123456789abcdef" for c in tid)
