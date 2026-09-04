"""案件接口：创建（202 异步受理）/详情/人工审批（主管权限）。"""
import hashlib
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.schemas import (
    CaseDetailOut,
    CreateCaseRequest,
    CreateCaseResponse,
    DecisionRequest,
    DecisionResponse,
)
from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import (
    ROLE_MANAGER,
    TOKEN_TYPE_BUYER,
    Principal,
    get_current_principal,
    get_current_user,
    require_roles,
)
from app.domain.models import AuditLog, CaseEvidence, Customer, Order, RefundCase, User
from app.domain.status import CaseStatus
from app.infrastructure.idempotency import build_key, execute_idempotent, hash_request
from app.infrastructure.lock import DistributedLock
from app.workflow.graph import resume_workflow

router = APIRouter(prefix="/api/v1/cases", tags=["cases"])


def _save_upload(file: UploadFile) -> tuple[str, str]:
    """保存上传凭证到本地 uploads/ 目录，返回 (可服务的 URL 路径, 字节 SHA-256)。

    L-1 修复：原本保存后调用方再读 file.file.read() 拿到的是空字节
    （流已被 write_bytes 消费），导致 file_hash 恒为空串的哈希。改为
    一次性读出完整字节，同时供落盘与哈希，双份验证。
    L-5 修复：返回 `/uploads/{name}`（前导斜杠）而非相对 `uploads/{name}`。
    上传目录由 main.py 静态挂载在 `/uploads`，前端 `<img>`/下载直接可访问。
    """
    from pathlib import Path

    from app.infrastructure.storage import get_upload_dir

    ext = Path(file.filename or "img").suffix[:10] or ".img"
    name = f"{uuid.uuid4().hex}{ext}"
    path = get_upload_dir() / name
    data = file.file.read()
    path.write_bytes(data)
    return f"/uploads/{name}", hashlib.sha256(data).hexdigest()


def _gen_ticket_no() -> str:
    return f"T{datetime.now():%Y%m%d%H%M%S}{uuid.uuid4().hex[:6].upper()}"


def _resolve_order(db: Session, order_id: str) -> Order | None:
    """按 order_no 或主键 id 解析订单（与 order_verify 兜底一致）。

    前端 mapOrder 把后端自增 id 直接当 order_id 传，故兼容两类标识。
    """
    order = db.query(Order).filter_by(order_no=order_id).first()
    if order is None and order_id.strip().isdigit():
        order = db.get(Order, int(order_id))
    return order


@router.post("", response_model=CreateCaseResponse, status_code=202)
def create_case(
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    applicant_id: Annotated[str, Form()],
    order_id: Annotated[str, Form()],
    applicant_amount: Annotated[int, Form(ge=0)],
    actual_amount: Annotated[int, Form(ge=0)],
    description: Annotated[str, Form()] = "",
    claim_type: Annotated[str, Form()] = "",
    image: Annotated[UploadFile | None, File()] = None,
    x_idempotency_key: Annotated[str | None, Header(alias="X-Idempotency-Key")] = None,
):
    """创建退款案件：快速受理返回 202；OCR/风险/工作流由 Worker 异步执行（Phase 4/5/6）。"""
    settings = get_settings()

    # 幂等键缺失时服务端生成（防资损兜底；前端正常应携带，Phase 7 完整校验）
    idem_key = x_idempotency_key or f"server-{uuid.uuid4().hex}"

    # 金额硬校验（裁决 D-012）：退款金额不得超过订单实付金额
    if applicant_amount > actual_amount:
        raise HTTPException(
            status_code=422,
            detail={"code": "AMOUNT_EXCEEDS", "message": "退款金额不能超过订单实付金额"},
        )

    # 越权防护（M-1）：BUYER 身份强制申请人为 token 携带的手机号本人，
    # 不信任客户端表单传入的 applicant_id；且只能对属于自己的订单发起退款。
    # STAFF 身份（客服/主管代客建单）不受限，但订单所属校验在 order_verify 兜底。
    if principal.token_type == TOKEN_TYPE_BUYER:
        applicant_id = principal.sub
        order = _resolve_order(db, order_id)
        if order is not None:
            customer = db.query(Customer).filter_by(phone=applicant_id).first()
            if customer is None or order.customer_id != customer.id:
                raise HTTPException(
                    status_code=403,
                    detail={"code": "ORDER_NOT_OWNED", "message": "订单不属于当前买家，禁止越权退款"},
                )

    # 买家端显式三选一（退款/退货退款/换货）→ claim_type 落库；员工侧建单为空字符串，存 NULL
    claim = claim_type.strip() if claim_type else ""
    if claim not in ("退款", "退货退款", "换货"):
        claim = ""

    case = RefundCase(
        ticket_no=_gen_ticket_no(),
        applicant_id=applicant_id,
        order_id=order_id,
        applicant_amount=applicant_amount,
        actual_amount=actual_amount,
        description=description,
        claim_type=claim or None,
        status=CaseStatus.CREATED.value,
        idempotency_key=idem_key,
    )
    db.add(case)
    db.flush()

    if image is not None and image.filename:
        url, file_hash = _save_upload(image)
        db.add(
            CaseEvidence(
                case_id=case.id,
                image_url=url,
                file_hash=file_hash,
                parse_status="OK",
            )
        )
        db.flush()

    db.add(
        AuditLog(
            case_id=case.id,
            from_status=None,
            to_status=CaseStatus.CREATED.value,
            operator=principal.sub,
            idempotency_key=idem_key,
        )
    )
    db.commit()

    # 生产者：写入 Redis Streams 交由 Worker 异步处理（Phase 4 链路）
    # 测试模式（TESTING=1）跳过入队，避免与后台 Worker 竞争
    import os

    if os.environ.get("TESTING") != "1":
        from app.infrastructure.streams import publish_case

        publish_case(case.id)

    return CreateCaseResponse(
        case_id=case.id, ticket_no=case.ticket_no, status=case.status
    )


@router.get("")
def list_cases(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """案件列表（分页 + 状态筛选）。"""
    q = db.query(RefundCase)
    if status:
        q = q.filter(RefundCase.status == status)
    total = q.count()
    items = (
        q.order_by(RefundCase.created_at.desc())
        .limit(min(limit, 200))
        .offset(max(offset, 0))
        .all()
    )
    return {
        "total": total,
        "items": [
            {
                "id": c.id,
                "ticket_no": c.ticket_no,
                "order_id": c.order_id,
                "applicant_amount": c.applicant_amount,
                "status": c.status,
                "risk_score": float(c.risk_score) if c.risk_score is not None else None,
                "sentiment_score": float(c.sentiment_score) if c.sentiment_score is not None else None,
                "description": c.description,
                "decision": c.decision,
                "review_reason": c.review_reason,
                # 工单8 意图识别（前端列表/详情展示）
                "intent": c.intent,
                "intent_source": c.intent_source,
                "intent_confidence": float(c.intent_confidence) if c.intent_confidence is not None else None,
                "intent_fallback": c.intent_fallback,
                "claim_type": c.claim_type,
                "created_at": c.created_at,
            }
            for c in items
        ],
    }


@router.get("/{case_id}/graph")
def get_case_graph(
    case_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """Agent 决策链路（大屏流转图数据源）。"""
    case = db.get(RefundCase, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail={"code": "CASE_NOT_FOUND", "message": "案件不存在"})
    from app.domain.models import AgentRun

    runs = db.query(AgentRun).filter_by(case_id=case_id).order_by(AgentRun.id).all()
    return {
        "case_id": case.id,
        "status": case.status,
        "nodes": [
            {
                "agent": r.agent_name,
                "status": r.status,
                "error_tag": r.error_tag,
                "duration_ms": r.duration_ms,
                "input": r.input_json,  # 复盘：节点实际处理的输入快照
                "output": r.output_json,
                "finished_at": r.finished_at,
                # telemetry：节点内 LLM 真实 token（无 LLM 调用的节点为 None）
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
            }
            for r in runs
        ],
    }


@router.get("/{case_id}", response_model=CaseDetailOut)
def get_case(
    case_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    case = db.get(RefundCase, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail={"code": "CASE_NOT_FOUND", "message": "案件不存在"})
    return case


@router.post("/{case_id}/decision", response_model=DecisionResponse)
def decide_case(
    case_id: int,
    body: DecisionRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles(ROLE_MANAGER))],
    x_idempotency_key: Annotated[str | None, Header(alias="X-Idempotency-Key")] = None,
):
    """主管人工审批（三层防重，裁决 D-006/D-014）：
    1. 幂等记录：同幂等键重复请求直接返回首次结果；
    2. 分布式锁：SETNX refund:approval:{case_id}，并发只有一个进入；
    3. 状态前置校验 + 图内乐观锁：已终态不可再审批。
    """
    settings = get_settings()
    client_key = x_idempotency_key or f"server-{uuid.uuid4().hex}"
    idem_key = build_key("approval", str(case_id), body.action, client_key)
    request_hash = hash_request({"case_id": case_id, "action": body.action, "comment": body.comment})

    def _approve() -> dict:
        case = db.get(RefundCase, case_id)
        if case is None:
            raise HTTPException(status_code=404, detail={"code": "CASE_NOT_FOUND", "message": "案件不存在"})

        # 状态前置校验：仅 SUSPENDED 可审批（已批准/拒绝/完成/失败均 409）
        if case.status != CaseStatus.SUSPENDED.value:
            raise HTTPException(
                status_code=409,
                detail={"code": "INVALID_STATE", "message": f"当前状态 {case.status} 不可审批，仅挂起工单可审批"},
            )

        # 分布式锁：并发审批只有一个成功（TTL 10s，Lua 校验 token 释放）
        lock = DistributedLock(
            f"{settings.approval_lock_prefix}:{case_id}", settings.approval_lock_ttl_ms
        )
        if not lock.acquire():
            raise HTTPException(
                status_code=409, detail={"code": "CONFLICT", "message": "该工单正在被处理，请勿重复操作"}
            )
        try:
            resume_workflow(case_id, body.action, body.comment, user.username)
        except ValueError as e:
            raise HTTPException(status_code=409, detail={"code": "CONFLICT", "message": str(e)}) from e
        finally:
            lock.release()

        db.refresh(case)
        return {"case_id": case.id, "status": case.status, "message": "审批完成"}

    first, result = execute_idempotent(idem_key, request_hash, _approve)
    if not first:
        # 幂等命中：返回首次结果（防资损关键路径：重复点击/网络重试不重复审批）
        if result is None:
            raise HTTPException(status_code=409, detail={"code": "IN_PROGRESS", "message": "审批处理中"})
        return DecisionResponse(**result)
    return DecisionResponse(**result)
