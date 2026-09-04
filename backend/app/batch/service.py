"""批量审批服务（需求文档 §3.1）：导出挂起订单 / 解析审批 Excel / 批量 resume / 汇总。

防资损防重（红线）：沿用单笔审批三层防重——幂等键 + 分布式锁（SETNX）+ 状态前置校验。
- 幂等键：同一批的同一单，重复上传/重复点击不会二次审批。
- 分布式锁：同一单并发批量与单笔审批互斥（复用 refund:approval:{case_id}）。
- 状态前置校验：非 SUSPENDED 跳过记失败，不整体回滚。
"""
import logging
import re
from pathlib import Path

from fastapi import HTTPException

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.domain.models import RefundCase
from app.domain.status import CaseStatus
from app.infrastructure.idempotency import build_key, execute_idempotent, hash_request
from app.infrastructure.lock import DistributedLock
from app.infrastructure.streams import get_sandbox_mode_runtime
from app.sandbox.adapter import (
    APPROVE_ACTION,
    REJECT_ACTION,
    parse_approval_excel_local,
    parse_approval_excel_sandbox,
)
from app.workflow.graph import resume_workflow

logger = logging.getLogger(__name__)

# 审批列（需求文档 §3.1 字段设计）
EXPORT_COLUMNS = [
    "ticket_no",        # 只读：工单号
    "case_id",          # 只读：定位挂起订单
    "order_id",         # 只读
    "applicant_amount", # 只读：申请退款金额（分）
    "actual_amount",    # 只读：实付金额（分）
    "description",      # 只读：客诉描述（截断）
    "ocr_summary",      # 只读：OCR 摘要
    "fraud_score",      # 只读：风险分
    "risk_level",       # 只读：舆情等级
    "action",           # 主管填：审批动作（同意/拒绝）
    "comment",          # 主管填：审批意见
]

_HEADER_LABELS = {
    "ticket_no": "工单号",
    "case_id": "案件ID",
    "order_id": "订单号",
    "applicant_amount": "申请退款金额(分)",
    "actual_amount": "实付金额(分)",
    "description": "客诉描述",
    "ocr_summary": "OCR摘要",
    "fraud_score": "风险分",
    "risk_level": "舆情等级",
    "action": "审批动作(同意/拒绝)",
    "comment": "审批意见",
}


# openpyxl 会拒收 XML 1.0 非法控制字符（\x00-\x08 / \x0b / \x0c / \x0e-\x1f），
# OCR 原文或客诉描述一旦混入这类字节，写入单元格会抛 IllegalCharacterError -> 500。
# 写单元格前统一剔除，保证「导出挂起订单」永不因恶意/脏内容而失败。
_XML_ILLEGAL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _sanitize_cell(value):
    """剔除 XML 非法控制字符；非字符串原样返回（金额/分数/空值保持原类型）。"""
    if isinstance(value, str):
        return _XML_ILLEGAL_RE.sub("", value)
    return value


def _export_suspended_rows() -> list[dict]:
    """直查库取 SUSPENDED 挂起单（无需反序列化 checkpoint）。"""
    db = SessionLocal()
    try:
        cases = (
            db.query(RefundCase)
            .filter(RefundCase.status == CaseStatus.SUSPENDED.value)
            .order_by(RefundCase.created_at.asc())
            .limit(get_settings().approval_export_limit)
            .all()
        )
        rows = []
        for c in cases:
            ocr = c.evidences[0] if c.evidences else None
            risk = c.risk_score if c.risk_score is not None else None
            risk_level = ""
            if risk is not None:
                if risk <= get_settings().risk_auto_max:
                    risk_level = "LOW"
                elif risk <= get_settings().risk_review_max:
                    risk_level = "MEDIUM"
                else:
                    risk_level = "HIGH"
            rows.append(
                {
                    "ticket_no": c.ticket_no,
                    "case_id": c.id,
                    "order_id": c.order_id,
                    "applicant_amount": c.applicant_amount,
                    "actual_amount": c.actual_amount,
                    "description": (c.description or "")[:200],
                    "ocr_summary": (ocr.ocr_text or "")[:200] if ocr else "",
                    "fraud_score": float(c.fraud_score) if c.fraud_score is not None else "",
                    "risk_level": risk_level,
                    "action": "",
                    "comment": "",
                }
            )
        return rows
    finally:
        db.close()


def export_suspended_excel() -> tuple[bytes, str]:
    """导出挂起订单 Excel（内存生成，返回 (bytes, filename)）。

    主管填写列「审批动作」带下拉列表（同意/拒绝），免手输，杜绝非法动作。
    """
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    rows = _export_suspended_rows()
    wb = Workbook()
    ws = wb.active
    ws.title = "挂起审批"

    # 表头（中文标签 + 英文列名注释）
    headers = [_HEADER_LABELS.get(c, c) for c in EXPORT_COLUMNS]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid")

    for r in rows:
        ws.append([_sanitize_cell(r.get(c, "")) for c in EXPORT_COLUMNS])

    # 审批动作下拉列表：值与解析端 _ACTION_MAP（同意→APPROVE / 拒绝→REJECT）严格一致，
    # 主管直接选择，避免手输空格/错字导致「非法审批动作」。
    # 覆盖到 J1000：即便当前无挂起单（空导出），主管补录行也能看到下拉箭头，
    # 不会误以为功能缺失。
    action_col = EXPORT_COLUMNS.index("action") + 1
    action_col_letter = ws.cell(row=1, column=action_col).column_letter
    dv = DataValidation(
        type="list",
        formula1='\"同意,拒绝\"',
        allow_blank=True,
        showErrorMessage=True,
        errorTitle="非法审批动作",
        error="请从下拉列表选择：同意 或 拒绝",
    )
    ws.add_data_validation(dv)
    dv.add(f"{action_col_letter}2:{action_col_letter}{max(2, 1 + len(rows), 1000)}")

    # 列宽（中文列宽加大）
    for i, c in enumerate(EXPORT_COLUMNS, start=1):
        label = _HEADER_LABELS.get(c, c)
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = max(12, len(label) * 2 + 6)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"suspended_{len(rows)}.xlsx"
    return buf.getvalue(), filename


def _resolve_case_id(raw_case_id: str, ticket_no: str | None = None) -> int | None:
    """把解析出的 case_id 转成数字案件ID：数字直接用；工单号（T2026...）回查数据库。"""
    raw = str(raw_case_id or "").strip()
    if raw.isdigit():
        return int(raw)
    db = SessionLocal()
    try:
        if ticket_no:
            case = db.query(RefundCase).filter_by(ticket_no=str(ticket_no).strip()).first()
            return case.id if case else None
        case = db.query(RefundCase).filter_by(ticket_no=raw).first()
        return case.id if case else None
    finally:
        db.close()


def _decide_action_single(case_id: int, action: str, comment: str, operator: str) -> dict:
    """单条批量审批（复用单笔审批三层防重 + resume_workflow）。返回汇总行。"""
    settings = get_settings()
    db = SessionLocal()
    try:
        case = db.get(RefundCase, case_id)
        if case is None:
            return {"case_id": case_id, "status": "FAILED", "message": "案件不存在"}
        if case.status != CaseStatus.SUSPENDED.value:
            return {
                "case_id": case_id,
                "status": "SKIPPED",
                "message": f"状态 {case.status} 已非挂起，跳过（防重）",
            }

        # 幂等键：actor:approval:case_id:action:client_key（client_key=批量任务批次号）
        idem_key = build_key("approval", str(case_id), action, f"batch-{operator}")
        request_hash = hash_request({"case_id": case_id, "action": action, "comment": comment})

        def _approve() -> dict:
            # 分布式锁：与单笔审批互斥（同一 prefix，防主管在 Excel 与界面同时点）
            lock = DistributedLock(
                f"{settings.approval_lock_prefix}:{case_id}", settings.approval_lock_ttl_ms
            )
            if not lock.acquire():
                return {"case_id": case_id, "status": "FAILED", "message": "该单正在被单笔处理，跳过"}
            try:
                resume_workflow(case_id, action, comment, operator)
            finally:
                lock.release()
            db.refresh(case)
            return {
                "case_id": case_id,
                "status": "APPROVED" if action == APPROVE_ACTION else "REJECTED",
                "message": f"审批完成 -> {case.status}",
            }

        first, result = execute_idempotent(idem_key, request_hash, _approve)
        if not first:
            if result is None:
                return {"case_id": case_id, "status": "FAILED", "message": "审批处理中"}
            return {"case_id": case_id, "status": "DUPLICATED", "message": "幂等命中，跳过重复审批"}
        return result
    finally:
        db.close()


def process_batch_approval(
    excel_path: Path,
    operator: str,
    sandbox_mode: str | None = None,
) -> dict:
    """批量审批主流程：沙箱/本地解析 Excel -> 逐条 resume -> 汇总。

    sandbox_mode 解析优先级（运行时开关）：
      1) 请求显式传参（sandbox_mode 非空）最优先；
      2) Redis 运行时开关（GET/PUT /batch/sandbox-mode 持久化值）；
      3) 回落配置默认（.env SANDBOX_MODE）。
    """
    cfg = get_settings()
    if sandbox_mode and sandbox_mode.strip():
        mode = sandbox_mode.strip().lower()
    else:
        runtime = get_sandbox_mode_runtime()
        mode = runtime if runtime in ("on", "off") else cfg.sandbox_mode

    if mode == "on":
        rows = parse_approval_excel_sandbox(excel_path)
    elif mode == "off":
        rows = parse_approval_excel_local(excel_path)
    else:
        raise HTTPException(
            status_code=400,
            detail={"code": "BAD_SANDBOX_MODE", "message": f"非法 SANDBOX_MODE: {mode!r}"},
        )

    valid, invalid = [], []
    for r in rows:
        if r.get("error"):
            invalid.append({"case_id": r["case_id"], "message": r["error"]})
        elif r.get("action") not in (APPROVE_ACTION, REJECT_ACTION):
            invalid.append({"case_id": r["case_id"], "message": "缺少审批动作"})
        else:
            valid.append(r)

    results = []
    for r in valid:
        case_id = _resolve_case_id(r["case_id"], r.get("ticket_no"))
        if case_id is None:
            results.append({"case_id": r["case_id"], "status": "FAILED", "message": "案件未找到（case_id 或 工单号 均无法定位）"})
            continue
        results.append(_decide_action_single(case_id, r["action"], r["comment"], operator))

    summary = {
        "approved": sum(1 for x in results if x["status"] == "APPROVED"),
        "rejected": sum(1 for x in results if x["status"] == "REJECTED"),
        "skipped": sum(1 for x in results if x["status"] == "SKIPPED"),
        "duplicated": sum(1 for x in results if x["status"] == "DUPLICATED"),
        "failed": sum(1 for x in results if x["status"] == "FAILED"),
        "invalid": len(invalid),
        "total": len(valid) + len(invalid),
    }
    return {
        "sandbox_mode": mode,
        "summary": summary,
        "rows": results + invalid,
    }
