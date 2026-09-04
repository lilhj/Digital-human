"""「导出挂起订单」接口回归测试：GET /api/v1/batch/export。

覆盖本次故障的两条根因（用户症状：主管点击导出 -> 500「服务器内部错误」）：
1. 权限：仅 MANAGER 可导出（客服 token 403，不影响业务码）；
2. 脏数据防御：挂起单 description/OCR 若含 XML 1.0 非法控制字符
   （\x00-\x08 / \x0b / \x0c / \x0e-\x1f），openpyxl 写单元格会抛
   IllegalCharacterError -> 500。修复后导出必须 200 且内容被清洗；
3. 导出结果为可被重载的合法 xlsx（zip 魔数 + load_workbook 可读）。
"""
import uuid
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.core.database import SessionLocal
from app.domain.models import CaseEvidence, RefundCase
from app.main import app

client = TestClient(app)

MARKER = f"bx-{uuid.uuid4().hex[:8]}"
_EXPORT_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# openpyxl 拒收的 XML 1.0 非法控制字符（本即用户环境 500 的潜在数据源）。
# 注意排除 \x00：PostgreSQL 在 UPDATE/INSERT 时就拒绝 NUL 字节（上游第一道防线），
# 真正可能落库的是 BEL/VT/FF/US 等其余控制字符。
_XML_ILLEGAL = "\x07\x0b\x0c\x1f"


@pytest.fixture(autouse=True)
def cleanup():
    yield
    db = SessionLocal()
    try:
        case_ids = [
            c.id
            for c in db.query(RefundCase).filter(RefundCase.ticket_no.like(f"{MARKER}%")).all()
        ]
        if case_ids:
            db.query(CaseEvidence).filter(CaseEvidence.case_id.in_(case_ids)).delete(
                synchronize_session=False
            )
            db.query(RefundCase).filter(RefundCase.id.in_(case_ids)).delete(
                synchronize_session=False
            )
            db.commit()
    finally:
        db.close()


def _seed_suspended_case(description: str, ocr_text: str | None = None) -> str:
    """直插一条 SUSPENDED 案件（等价于工作流挂起后、主管导出前的状态），返回工单号。"""
    db = SessionLocal()
    try:
        case = RefundCase(
            ticket_no=f"{MARKER}{uuid.uuid4().hex[:6].upper()}",
            applicant_id=f"{MARKER}buyer",
            order_id=f"order-{uuid.uuid4().hex[:8]}",
            applicant_amount=1000,
            actual_amount=1000,
            description=description[:200],  # 导出端同样截断到 200
            status="SUSPENDED",
            idempotency_key=f"{MARKER}-{uuid.uuid4().hex}",
        )
        db.add(case)
        db.flush()  # 拿到 case.id
        if ocr_text:
            db.add(
                CaseEvidence(
                    case_id=case.id,
                    image_url=f"/uploads/{MARKER}r.png",
                    ocr_text=ocr_text,
                )
            )
        db.commit()
        return case.ticket_no
    finally:
        db.close()


def _staff_token(username: str, password: str) -> str:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _export_rows(body: bytes) -> list[tuple]:
    """把导出字节流重载回 workbook，返回全部数据行。"""
    wb = load_workbook(BytesIO(body))
    ws = wb.active
    return list(ws.iter_rows(values_only=True))


def test_export_requires_manager():
    """非主管（客服）无权导出：403，不会走到业务逻辑/依赖。"""
    tok = _staff_token("csr", "csr123")
    r = client.get("/api/v1/batch/export", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403


def test_export_returns_valid_xlsx():
    """主管导出：200 + 正确 MIME + zip 魔数 + 文件可重载且含挂起工单。"""
    ticket = _seed_suspended_case("正常描述")
    tok = _staff_token("manager", "manager123")
    r = client.get("/api/v1/batch/export", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith(_EXPORT_MIME)
    assert "attachment" in r.headers.get("content-disposition", "")
    assert "suspended_" in r.headers.get("content-disposition", "")
    body = r.content
    assert body[:2] == b"PK"  # zip 魔数

    rows = _export_rows(body)
    header = [str(h) for h in rows[0]]
    col = header.index("工单号")
    found = [row for row in rows[1:] if row[col] == ticket]
    assert found, "导出未包含种下的挂起单"


def test_export_sanitizes_illegal_control_chars():
    """脏数据防御：描述/OCR 含 XML 非法控制字符时导出不 500，内容被清洗。

    修复前：openpyxl 写单元格遇 \x00-\x08/\x0b\x0c/\x0e-\x1f 抛
    IllegalCharacterError -> 500「服务器内部错误」（用户的原始症状）。
    """
    dirty = f"描述含非法字节{_XML_ILLEGAL}请清洗"
    dirty_ocr = f"OCR原文{_XML_ILLEGAL}脱敏"
    ticket = _seed_suspended_case(description=dirty, ocr_text=dirty_ocr)
    tok = _staff_token("manager", "manager123")
    r = client.get("/api/v1/batch/export", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text

    rows = _export_rows(r.content)
    header = [str(h) for h in rows[0]]
    tix_col = header.index("工单号")
    desc_col = header.index("客诉描述")
    ocr_col = header.index("OCR摘要")

    row = next(row for row in rows[1:] if row[tix_col] == ticket)
    assert "请清洗" in str(row[desc_col]), f"描述被截断/丢失: {row[desc_col]!r}"
    assert "脱敏" in str(row[ocr_col]), f"OCR 被截断/丢失: {row[ocr_col]!r}"
    # 非法控制字符必须被剔除
    assert all(_XML_ILLEGAL not in str(cell) for cell in row[desc_col : desc_col + 1])
    assert all(_XML_ILLEGAL not in str(cell) for cell in row[ocr_col : ocr_col + 1])