"""Redis Streams 基础设施：生产者-消费者（规格 06 第 11 节）。

链路：FastAPI -> Stream(complaint_stream) -> Worker(Consumer Group)
      -> 失败重投主流（retry_count+1，上限 3）-> 超限死信(complaint_stream_dlq)

说明（工程取舍记录）：MVP 未实现 Outbox 事务模式——创建案件先写 PG
（CREATED + 审计）再 XADD 入队；若 XADD 失败，创建接口返回 202 但案件
停于 CREATED，由补偿任务兜底（Phase 10 健康检查扫描）。事件源不缺失，
PG 为业务状态唯一真相（裁决 D-005）。
"""
import json
import uuid
from typing import Any

import redis

from app.core.config import get_settings

STREAM = "complaint_stream"
STREAM_DLQ = "complaint_stream_dlq"
GROUP = "refund-workers"
MAX_RETRIES = 3  # 业务失败重试上限（规格 06 第 8 节）

# 沙箱模式运行时开关的 Redis 键（存值：'on'/'off'）
SANDBOX_MODE_KEY = "refund_v2:sandbox_mode"


def get_sandbox_mode_runtime() -> str | None:
    """读运行时沙箱模式（Redis）。从未设置过返回 None（上层回落配置默认）。"""
    return get_redis().get(SANDBOX_MODE_KEY)


def set_sandbox_mode_runtime(mode: str) -> None:
    """写运行时沙箱模式（Redis 持久化，重启不丢、多进程共享）。"""
    get_redis().set(SANDBOX_MODE_KEY, mode)

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        # socket_timeout=30：XREADGROUP 会自动把 socket 超时设为 block+2，
        # 显式加大避免空流轮询时的偶发 TimeoutError
        _client = redis.Redis.from_url(
            get_settings().redis_url, decode_responses=True, socket_timeout=30
        )
    return _client


def ensure_consumer_group(stream: str = STREAM) -> None:
    """幂等创建消费组（BUSYGROUP 可忽略）。"""
    r = get_redis()
    try:
        r.xgroup_create(stream, GROUP, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


def publish_case(case_id: int, trace_id: str | None = None) -> str:
    """生产者：案件入队。"""
    r = get_redis()
    ensure_consumer_group()
    return r.xadd(
        STREAM,
        {
            "case_id": case_id,
            "trace_id": trace_id or uuid.uuid4().hex,
            "retry_count": 0,
        },
    )


def publish_retry(payload: dict[str, Any]) -> str:
    """重试：重投主流（同消费组自然再次消费），retry_count+1。"""
    r = get_redis()
    ensure_consumer_group()
    payload["retry_count"] = int(payload.get("retry_count", 0)) + 1
    return r.xadd(STREAM, payload)


def publish_dlq(payload: dict[str, Any]) -> str:
    """死信：重试耗尽，供人工排查。"""
    r = get_redis()
    ensure_consumer_group(STREAM_DLQ)
    return r.xadd(STREAM_DLQ, payload)


def read_batch(consumer: str, count: int = 10, block_ms: int = 2000) -> list[dict[str, Any]]:
    """消费者：从组读取一批消息。"""
    r = get_redis()
    ensure_consumer_group()
    resp = r.xreadgroup(GROUP, consumer, {STREAM: ">"}, count=count, block=block_ms)
    messages: list[dict[str, Any]] = []
    if resp:
        for stream_name, entries in resp:
            for msg_id, fields in entries:
                messages.append({"msg_id": msg_id, "stream": stream_name, "fields": fields})
    return messages


def ack(message: dict[str, Any]) -> None:
    """确认消息（防止重启后重复消费）。"""
    get_redis().xack(message["stream"], GROUP, message["msg_id"])


def pending_count() -> int:
    """未确认消息数（XPENDING），用于监控 Worker 中断积压。"""
    r = get_redis()
    ensure_consumer_group()
    try:
        return int(r.xpending(STREAM, GROUP)["pending"])
    except redis.ResponseError:
        return 0
