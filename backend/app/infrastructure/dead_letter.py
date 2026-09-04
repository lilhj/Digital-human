"""死信队列（工单8 异常兜底）：多次失败/异常的任务入队，避免阻塞主链路。

设计（对齐 PRD §4.2 + 现有 suspend 快照双写降级策略）：
- Redis 兜底降级：Redis 不可用仅 warning，不抛出——死信是辅助可观测设施，绝不能反客为主阻断主流程。
- 入队内容含 stage/case_id/payload/error/ts，便于排障与周期审计。
"""
import json
import logging
import time

from app.infrastructure.streams import get_redis

logger = logging.getLogger("dead_letter")

DLQ_KEY = "refund:dead_letter"


def enqueue(stage: str, case_id: int | None, payload: dict, error: str) -> None:
    """将一次失败/兜底事件写入死信队列（Redis list，最右压入）。

    异常降级：Redis 连接/序列化问题仅告警，绝不抛出。
    """
    item = {
        "stage": stage,
        "case_id": case_id,
        "payload": payload,
        "error": str(error),
        "ts": time.time(),
    }
    try:
        redis = get_redis()
        redis.rpush(DLQ_KEY, json.dumps(item, ensure_ascii=False, default=str))
        logger.info("[dead_letter] stage=%s case_id=%s 已入队", stage, case_id)
    except Exception as e:  # noqa: BLE001 - 辅助队列降级，非阻断
        logger.warning("[dead_letter] 入队失败（非阻断）: %s", e)


def length() -> int:
    """死信队列当前长度（运维/周期报告用）。"""
    try:
        return get_redis().llen(DLQ_KEY)
    except Exception:  # noqa: BLE001
        return 0


def drain(limit: int = 100) -> list[dict]:
    """取出（不删除）最近死信条目，供审计页面/报告展示。"""
    try:
        raws = get_redis().lrange(DLQ_KEY, -limit, -1)
        return [json.loads(r) for r in raws]
    except Exception:  # noqa: BLE001
        return []
