"""SSE 事件流：GET /api/v1/cases/{case_id}/events。

Worker 将状态变更发布到 Redis Pub/Sub，本端点订阅并转发给前端大屏。
前端断线重连由客户端处理（浏览器 EventSource 自动重连）。
鉴权：EventSource 不能带自定义 header，支持 ?token= 查询参数（与 header 等价）。
"""
import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials

from app.core.database import get_db
from app.core.security import bearer_scheme, decode_token
from app.domain.models import User
from app.infrastructure.streams import get_redis
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/v1/cases", tags=["events"])


@router.get("/{case_id}/events")
async def case_events(
    case_id: int,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
    token: Annotated[str | None, Query()] = None,
    db: Annotated[Session, Depends(get_db)] = None,
):
    token_value = creds.credentials if creds else token
    if not token_value:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "未登录"})
    payload = decode_token(token_value)
    user = db.query(User).filter_by(username=payload["sub"], is_active=True).first()
    if user is None:
        raise HTTPException(status_code=401, detail={"code": "USER_NOT_FOUND", "message": "用户不存在"})
    """SSE 流：连接成功后先推送一条 heartbeat，随后实时转发案件事件。"""
    channel = f"case:{case_id}"

    async def event_generator():
        r = get_redis()
        pubsub = r.pubsub()
        pubsub.subscribe(channel)

        def format_sse(payload: str, event: str = "message") -> str:
            return f"event: {event}\ndata: {payload}\n\n"

        try:
            # 初始心跳（证明连接建立；也用于前端判断断线重连）
            yield format_sse(json.dumps({"type": "HEARTBEAT", "case_id": case_id}), "heartbeat")
            loop = asyncio.get_event_loop()
            while True:
                raw = await loop.run_in_executor(None, pubsub.get_message, True, 5.0)
                if raw is None:
                    yield ": keep-alive\n\n"
                    continue
                if raw.get("type") == "message":
                    yield format_sse(raw["data"])
        finally:
            pubsub.unsubscribe(channel)
            pubsub.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
