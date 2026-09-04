"""LangGraph Checkpointer（裁决 D-005：PostgreSQL 为最终状态，金融级不丢失）。

线程/进程安全：
- PostgresSaver.setup() 内部执行 CREATE INDEX CONCURRENTLY，多进程并发会互相阻塞
  -> setup 全进程只执行一次（threading.Lock 保护）
- sync PostgresSaver 连接非线程安全 -> 每次调用创建独立连接（每次 invoke 新建）
"""
import threading

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver

from app.core.config import get_settings

_setup_lock = threading.Lock()
_setup_done = False


def psycopg_dsn() -> str:
    return get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")


def create_checkpointer() -> PostgresSaver:
    """创建 Checkpointer：每次新建独立连接（线程安全）；setup 全进程仅一次。"""
    global _setup_done
    conn = psycopg.connect(psycopg_dsn(), autocommit=True)
    saver = PostgresSaver(conn)
    if not _setup_done:
        with _setup_lock:
            if not _setup_done:
                saver.setup()
                _setup_done = True
    return saver
