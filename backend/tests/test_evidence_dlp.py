"""L-4 修复：OCR 文本 PII 落库脱敏（工单6 DLP）。

旧缺陷：evidence_node 把 OCR 识别的原文（收据/订单上的手机号、身份证）直接写入
case_evidence.ocr_text，前端详情页按原文展示 —— 明文 PII 落库 + 外显。

验证三件事：
1. 落库的 ocr_text 已脱敏（手机号/身份证打星），原文不再持久化；
2. 工作流 state 仍以原文喂给风险模型（判分为准，不被脱敏干扰）；
3. AgentRun 快照对 evidence_text 同样脱敏（日志不泄明）。
"""
import uuid

import pytest

from app.agents.providers import OcrProvider, OcrResult
from app.core.database import SessionLocal
from app.domain.models import AgentRun, CaseEvidence, RefundCase
from app.domain.status import CaseStatus
from app.security.dlp import RuleBasedDlpProvider
from app.workflow import nodes
from app.workflow.graph import run_workflow
from tests.conftest import cleanup_cases_by_marker

MARKER = f"evd-{uuid.uuid4().hex[:8]}"

_LINE = (
    "客户：张三 手机号 13800000000\n"
    "身份证 110101199001011234\n"
    "退款：破损商品 发票金额 128 元"
)


class _PiiOcrProvider(OcrProvider):
    """固定返回含手机号/身份证的 OCR 文本（模拟真实收据识别）。"""

    def extract(self, image_path: str) -> OcrResult:
        return OcrResult(text=_LINE, confidence=0.95, status="OK")


@pytest.fixture(autouse=True)
def cleanup():
    yield
    cleanup_cases_by_marker(MARKER)


@pytest.fixture(autouse=True)
def _rule_based_dlp(monkeypatch):
    """脱敏 Provider 锁定规则引擎，保证断言确定（开发库可能配置 Noop）。"""
    monkeypatch.setattr(nodes, "dlp_provider", RuleBasedDlpProvider())


@pytest.fixture(autouse=True)
def _pii_ocr(monkeypatch):
    monkeypatch.setattr(nodes, "ocr_provider", _PiiOcrProvider())
    return _PiiOcrProvider()


def _make_case_with_evidence() -> int:
    db = SessionLocal()
    try:
        c = RefundCase(
            ticket_no=f"E{uuid.uuid4().hex[:10].upper()}",
            applicant_id=MARKER,
            order_id="order-wf",
            applicant_amount=12_800,
            actual_amount=12_800,
            description="商品破损，申请退款",
            status=CaseStatus.CREATED.value,
            idempotency_key=f"evd-{uuid.uuid4().hex}",
        )
        db.add(c)
        db.flush()
        db.add(
            CaseEvidence(
                case_id=c.id,
                image_url="uploads/test-receipt.jpg",
                parse_status="OK",
            )
        )
        db.commit()
        return c.id
    finally:
        db.close()


class TestOcrPiiMasked:
    def test_ocr_text_stored_masked_without_raw_pii(self):
        """落库 ocr_text 手机号/身份证打星，原文手机号不再出现。"""
        case_id = _make_case_with_evidence()
        result = run_workflow(case_id, f"trace-{uuid.uuid4().hex}")
        # 低金额 + 高置信 + 有凭证：自动批准走完
        assert result["decision"] == "APPROVE"

        db = SessionLocal()
        try:
            ev = db.query(CaseEvidence).filter_by(case_id=case_id).first()
            assert ev is not None and ev.ocr_text is not None
            assert "13800000000" not in ev.ocr_text, "手机号原文泄露到 ocr_text"
            assert "138****0000" in ev.ocr_text
            assert "110101199001011234" not in ev.ocr_text, "身份证原文泄露到 ocr_text"
            assert "110101********1234" in ev.ocr_text
        finally:
            db.close()

    def test_evidence_text_route_mask_from_all_intake(self):
        """AgentRun 轨迹对 evidence_text 必须脱敏（日志级不泄明）。"""
        case_id = _make_case_with_evidence()
        run_workflow(case_id, f"trace-{uuid.uuid4().hex}")

        db = SessionLocal()
        try:
            runs = db.query(AgentRun).filter_by(case_id=case_id).all()
            import json as _json

            joined = "\n".join(
                _json.dumps(r.input_json or {}) + _json.dumps(r.output_json or {})
                for r in runs
            )
            assert "13800000000" not in joined, "AgentRun 快照泄露手机号原文"
            assert "110101199001011234" not in joined, "AgentRun 快照泄露身份证原文"
        finally:
            db.close()