"""Worker 进程：消费 Redis Streams，驱动案件状态推进。

用法: cd backend && python -m app.worker.main

Loop 验证要求：
- Consumer Group 消费，XACK 确认（Worker 重启不重复处理）
- 幂等推进：仅 CREATED -> RUNNING（乐观锁 version），重复消息自动跳过
- 业务异常 -> 重试流（retry_count+1，上限 3）-> 死信流 + 案件 FAILED
- 同一案件不并发推进：依赖 PG 乐观锁
"""
import json
import logging
import time
from typing import Any

from sqlalchemy import update

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.domain.models import AuditLog, RefundCase
from app.domain.status import CaseStatus
from app.infrastructure import streams
from app.infrastructure.events import publish_event

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("worker")
settings = get_settings()


def process_case(case_id: int, trace_id: str) -> str:
    """处理一条案件消息：推进 CREATED -> RUNNING，然后执行 LangGraph 工作流。

    幂等：仅 CREATED 可推进（乐观锁）；重复消息自动跳过。
    工作流内部完成后续状态流转（SUSPENDED/APPROVED/REJECTED，人工挂起走 interrupt）。
    """
    from app.workflow.graph import run_workflow

    db = SessionLocal()
    try:
        case = db.get(RefundCase, case_id)
        if case is None:
            logger.warning("[%s] case %s 不存在，跳过", trace_id, case_id)
            return "SKIPPED"

        # 重复消息兜底：状态非 CREATED 直接跳过（不重复推进）
        if case.status != CaseStatus.CREATED.value:
            logger.info("[%s] case %s 状态 %s，重复消息跳过", trace_id, case_id, case.status)
            return "SKIPPED"

        # 乐观锁推进：并发消费同一案件只有一个成功
        result = db.execute(
            update(RefundCase)
            .where(
                RefundCase.id == case.id,
                RefundCase.status == CaseStatus.CREATED.value,
                RefundCase.version == case.version,
            )
            .values(status=CaseStatus.RUNNING.value, version=case.version + 1)
        )
        if result.rowcount == 0:
            logger.info("[%s] case %s 并发冲突，跳过", trace_id, case_id)
            return "SKIPPED"

        db.add(
            AuditLog(
                case_id=case.id,
                from_status=CaseStatus.CREATED.value,
                to_status=CaseStatus.RUNNING.value,
                operator="worker",
                idempotency_key=trace_id,
            )
        )
        db.commit()
        publish_event(case.id, "STATUS_CHANGED", {"status": CaseStatus.RUNNING.value})
        logger.info("[%s] case %s -> RUNNING", trace_id, case.id)

        # 执行 LangGraph 多 Agent 工作流（内部落库终态/挂起）
        run_workflow(case.id, trace_id)
        return "PROCESSED"
    finally:
        db.close()


def handle_message(message: dict[str, Any]) -> None:
    """单条消息处理：成功 ACK；失败按重试策略处理。"""
    fields = message["fields"]
    case_id = int(fields["case_id"])
    trace_id = fields.get("trace_id", "unknown")
    retry_count = int(fields.get("retry_count", 0))

    try:
        process_case(case_id, trace_id)
        streams.ack(message)
    except Exception as e:  # noqa: BLE001 - Worker 顶层兜底，按重试策略处理
        logger.error("[%s] case %s 处理失败: %s", trace_id, case_id, e)
        streams.ack(message)  # 先确认原消息，避免无限重投

        if retry_count >= streams.MAX_RETRIES:
            streams.publish_dlq({"case_id": case_id, "trace_id": trace_id, "error": str(e)})
            _mark_failed(case_id, trace_id, f"重试耗尽进入死信: {e}")
        else:
            streams.publish_retry(fields)
            logger.warning("[%s] case %s 重试 %s/%s", trace_id, case_id, retry_count + 1, streams.MAX_RETRIES)


def _mark_failed(case_id: int, trace_id: str, reason: str) -> None:
    """重试耗尽：案件 FAILED + 审计（终态，告警人工介入）。"""
    db = SessionLocal()
    try:
        case = db.get(RefundCase, case_id)
        if case is None:
            return
        db.execute(
            update(RefundCase)
            .where(RefundCase.id == case.id, RefundCase.version == case.version)
            .values(status=CaseStatus.FAILED.value, version=case.version + 1)
        )
        db.add(
            AuditLog(
                case_id=case.id,
                from_status=case.status,
                to_status=CaseStatus.FAILED.value,
                operator="worker",
                idempotency_key=trace_id,
            )
        )
        db.commit()
        publish_event(case.id, "STATUS_CHANGED", {"status": CaseStatus.FAILED.value, "reason": reason})
        logger.error("[%s] case %s 标记 FAILED: %s", trace_id, case_id, reason)
    finally:
        db.close()


def run_forever(consumer: str | None = None) -> None:
    """主循环：持续消费。consumer 名唯一（支持多 Worker）。"""
    import uuid

    from app.agents.factory import configure_providers

    configure_providers()  # 启动时初始化 OCR/风险 Provider（真实或 Fake）

    consumer_name = consumer or f"worker-{uuid.uuid4().hex[:6]}"
    logger.info("Worker %s 启动，监听 %s", consumer_name, streams.STREAM)
    while True:
        try:
            messages = streams.read_batch(consumer_name)
            for m in messages:
                handle_message(m)
        except Exception as e:  # noqa: BLE001
            logger.error("消费循环异常: %s（等待重试）", e)
            time.sleep(2)


if __name__ == "__main__":
    run_forever()
