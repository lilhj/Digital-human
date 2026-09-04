"""Worker 集成测试（真实 Redis Streams + PostgreSQL）：
正常消费、重复消息幂等、失败重试与死信、Worker 中断后消息可认领。
"""
import uuid

import pytest
from sqlalchemy import text

from app.core.database import SessionLocal
from app.domain.models import AgentRun, CaseEvidence, RefundCase
from app.domain.status import CaseStatus
from app.infrastructure import streams
from app.infrastructure.streams import get_redis
from app.worker import main as worker_main
from tests.conftest import cleanup_cases_by_marker

MARKER = f"worker-test-{uuid.uuid4().hex[:8]}"


def _reset_streams():
    """硬重置 Redis Streams：删流 + 销毁消费组，保证每个用例从干净状态起步。

    根因：原 cleanup 仅用例结束后删流，但共享消费组 refund-workers 的消费者偏移
    会跨 run 残留；一旦残留消息 + 陈旧偏移共存，XREADGROUP '>' 读不到新消息，
    导致 len(msgs)==0 的伪失败。改为用例开始前也重置流与组，彻底隔离。
    """
    r = get_redis()
    for s in (streams.STREAM, streams.STREAM_DLQ):
        r.delete(s)
        try:
            r.xgroup_destroy(s, streams.GROUP)
        except Exception:
            pass


@pytest.fixture(autouse=True)
def cleanup():
    _reset_streams()  # 开始时也重置，避免跨 run 残留污染
    yield
    cleanup_cases_by_marker(MARKER)
    r = get_redis()
    # 清理测试期间产生的流消息
    for s in (streams.STREAM, streams.STREAM_DLQ):
        r.delete(s)
    r.delete("pending-test-key")


def make_case(with_evidence: bool = False) -> int:
    db = SessionLocal()
    case = RefundCase(
        ticket_no=f"W{uuid.uuid4().hex[:10].upper()}",
        applicant_id=MARKER,
        order_id="order-w",
        applicant_amount=12_800,
        actual_amount=12_800,
        description="商品破损，申请退款",
        status=CaseStatus.CREATED.value,
        idempotency_key=f"w-{uuid.uuid4().hex}",
    )
    db.add(case)
    db.flush()
    if with_evidence:
        db.add(
            CaseEvidence(
                case_id=case.id,
                image_url="uploads/test.jpg",
                parse_status="OK",
            )
        )
    db.commit()
    case_id = case.id
    db.close()
    return case_id


def read_all(consumer: str) -> list[dict]:
    """持续读取直到空（避免 batch 边界问题）。"""
    msgs = streams.read_batch(consumer, count=100, block_ms=200)
    return msgs


class TestNormalConsume:
    def test_publish_consume_ack_process(self):
        """带有效凭证的 128 元案件：Worker 消费后完整跑通自动退款链路。"""
        case_id = make_case(with_evidence=True)
        streams.publish_case(case_id)

        msgs = read_all("t-consumer-1")
        assert len(msgs) == 1
        assert int(msgs[0]["fields"]["case_id"]) == case_id
        assert msgs[0]["fields"]["trace_id"]

        worker_main.handle_message(msgs[0])

        db = SessionLocal()
        try:
            case = db.get(RefundCase, case_id)
            # 自动批准 -> Mock 退款 -> COMPLETED
            assert case.status == CaseStatus.COMPLETED.value
            # 8 个图节点（INTAKE/INTENT/ORDER_VERIFY/EVIDENCE/FRAUD/SENTIMENT/DECISION/FINALIZE）
            # + 工单6 安全网关两个子步骤（CRITIC/DLP，内联在 intake 内单独落 trace）
            assert db.query(AgentRun).filter_by(case_id=case_id).count() == 10
        finally:
            db.close()
        # 已 ACK：无积压
        assert streams.pending_count() == 0

    def test_duplicate_message_does_not_advance_twice(self):
        """重复投递（Worker 崩溃重试）必须幂等：只推进一次。"""
        case_id = make_case(with_evidence=True)
        streams.publish_case(case_id)
        msgs = read_all("t-consumer-2")
        worker_main.handle_message(msgs[0])
        # 模拟消息重新投递（崩溃恢复后再次处理同一消息）
        worker_main.handle_message(msgs[0])

        db = SessionLocal()
        try:
            case = db.get(RefundCase, case_id)
            assert case.status == CaseStatus.COMPLETED.value
            # 重复消息被跳过：Agent 轨迹不重复（仍为 10 条：8 个图节点 + CRITIC/DLP 安全网关子步骤）
            assert db.query(AgentRun).filter_by(case_id=case_id).count() == 10
        finally:
            db.close()


class TestRetryAndDLQ:
    def test_failure_retries_then_dlq(self, monkeypatch):
        case_id = make_case()

        def boom(case_id, trace_id):
            raise RuntimeError("模拟业务故障")

        monkeypatch.setattr(worker_main, "process_case", boom)
        streams.publish_case(case_id)

        # MAX_RETRIES=3：第 1/2/3 次失败重投主流，第 4 次（retry_count=3）进死信
        for round_no in range(streams.MAX_RETRIES + 1):
            msgs = read_all(f"t-retry-{round_no}")
            assert len(msgs) == 1, f"第 {round_no + 1} 轮应读到消息"
            assert int(msgs[0]["fields"]["retry_count"]) == round_no
            worker_main.handle_message(msgs[0])

        # 死信流 1 条，案件 FAILED
        r = get_redis()
        dlq_len = r.xlen(streams.STREAM_DLQ)
        assert dlq_len == 1

        db = SessionLocal()
        try:
            case = db.get(RefundCase, case_id)
            assert case.status == CaseStatus.FAILED.value
        finally:
            db.close()


class TestCrashRecovery:
    def test_unacked_message_claimable_by_other_worker(self):
        """Worker 中断：未 ACK 消息进入 PENDING，可被其他 Worker XCLAIM 认领。"""
        case_id = make_case()
        streams.publish_case(case_id)

        # Worker A 读取但"崩溃"（不 ACK）
        msgs = read_all("t-crash-a")
        assert len(msgs) == 1
        assert streams.pending_count() == 1

        # Worker B 通过 XPENDING(带范围) + XCLAIM 认领（min-idle 0 模拟超时已过）
        r = get_redis()
        pending = r.xpending_range(streams.STREAM, streams.GROUP, "-", "+", 10)
        msg_id = pending[0]["message_id"]
        claimed = r.xclaim(streams.STREAM, streams.GROUP, "t-crash-b", 0, [msg_id])
        assert len(claimed) == 1
        # B 处理并 ACK
        worker_main.handle_message(msgs[0])
        assert streams.pending_count() == 0
