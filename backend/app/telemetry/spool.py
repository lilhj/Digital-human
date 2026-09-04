"""SQLite spool：Langfuse Trace 上报失败的本地缓存队列（工单5 移植）。

宿主进程独占读写（backend/var/spool/，沙箱不可达）。上报失败进入指数退避，
MAX_ATTEMPTS 后置 failed（不丢数据、不阻塞主流程）。
"""
import json
import sqlite3
import time
from pathlib import Path

MAX_ATTEMPTS = 5

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trace_spool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_json TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    next_retry_at REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
"""


class SpoolStore:
    def __init__(self, spool_dir: Path):
        self._dir = Path(spool_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._db = self._dir / "trace_spool.db"
        self._clock = time.time  # 可被测试替换
        with self._conn() as c:
            c.execute(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db)
        conn.row_factory = sqlite3.Row
        return conn

    def enqueue(self, trace_json: dict) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO trace_spool (trace_json, created_at) VALUES (?, ?)",
                (json.dumps(trace_json, ensure_ascii=False), self._clock()),
            )

    def pending(self, max_attempts: int = MAX_ATTEMPTS) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM trace_spool WHERE status='pending' AND next_retry_at <= ? AND attempts < ?",
                (self._clock(), max_attempts),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_success(self, ids: list[int]) -> None:
        if not ids:
            return
        with self._conn() as c:
            c.executemany("DELETE FROM trace_spool WHERE id=?", [(i,) for i in ids])

    def mark_backoff(self, ids: list[int], next_delta_s: float) -> None:
        if not ids:
            return
        with self._conn() as c:
            c.executemany(
                "UPDATE trace_spool SET attempts=attempts+1, "
                "status=CASE WHEN attempts+1 >= ? THEN 'failed' ELSE 'pending' END, "
                "next_retry_at=? WHERE id=?",
                [(MAX_ATTEMPTS, self._clock() + next_delta_s, i) for i in ids],
            )
