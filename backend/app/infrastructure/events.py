"""事件发布（Redis Pub/Sub）：Worker 状态变更 -> SSE 推送前端大屏（规格 06 第 11 节）。"""
import json
from typing import Any

from app.infrastructure.streams import get_redis

EVENT_CHANNEL = "case:{case_id}"


def publish_event(case_id: int, event_type: str, payload: dict[str, Any] | None = None) -> None:
    """发布案件事件（如 AGENT_FINISHED / STATUS_CHANGED / REVIEW_REQUIRED）。"""
    message = {"type": event_type, "case_id": case_id, "data": payload or {}}
    get_redis().publish(EVENT_CHANNEL.format(case_id=case_id), json.dumps(message, ensure_ascii=False))
