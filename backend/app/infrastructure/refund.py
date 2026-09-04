"""退款适配器（Loop 提示词 Phase 7：无真实支付凭证时只调用 MockRefundProvider）。

- 退款动作必须有独立幂等键（refund_records.idempotency_key 唯一约束）
- Mock：状态流转 APPROVED -> REFUNDING -> COMPLETED + 记录退款流水
- 失败处理（M-2 修复）：退款失败不再让案件**卡死在 APPROVED**。
  单次 execute 内置有界重试，按状态机推进：
      APPROVED -> REFUNDING -> COMPLETED                  （网关成功）
                     └---> REFUND_FAILED -> REFUNDING -> COMPLETED（重试成功）
                                          └---> FAILED（重试耗尽，终态）
- 真实网关接入点：RefundProvider 接口预留（execute 签名不变），gateway 在此替换。
"""
import logging
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import update

from app.core.database import SessionLocal
from app.domain.models import AuditLog, RefundCase, RefundRecord
from app.domain.status import CaseStatus
from app.infrastructure.events import publish_event
from app.infrastructure.idempotency import build_key, execute_idempotent

logger = logging.getLogger("refund")


@dataclass
class RefundResult:
    success: bool
    message: str


class RefundProvider:
    """退款接口。MVP 使用 Mock 实现；接入真实网关时替换此类。"""

    def execute(self, *, case_id: int, amount_cent: int, refund_key: str) -> RefundResult:
        raise NotImplementedError


class MockRefundProvider(RefundProvider):
    """Mock 退款：单次 execute 内有界重试 + 状态机闭环（M-2 修复）。

    相比旧实现的两点关键改进：
    1. **幂等失败不冻结**：每次重试用独立子键（build_key(refund_key, attempt)）。
       旧实现共用单一 refund_key，首次失败会被 execute_idempotent 缓存，
       之后同键重试永远命中"失败结果"，补偿/运营重试形同虚设。
    2. **失败必落终态**：重试耗尽 REFUND_FAILED -> FAILED（终态），
       调用方（finalize_node）再兜底，案件绝不停留在 APPROVED 模糊态。

    gateway(attempt) -> bool 可注入：默认恒成功；测试用 transient/all-fail 注入真实故障。
    """

    def __init__(
        self,
        *,
        gateway: Callable[[int], bool] | None = None,
        max_attempts: int = 3,
    ):
        self._gateway = gateway or (lambda attempt: True)
        self._max_attempts = max_attempts

    def execute(self, *, case_id: int, amount_cent: int, refund_key: str) -> RefundResult:
        db = SessionLocal()
        try:
            case = db.get(RefundCase, case_id)
            if case is None:
                return RefundResult(False, "案件不存在")
            # 防重复/重放：已终态直接返回
            if case.status == CaseStatus.COMPLETED.value:
                return RefundResult(True, "案件已完成，退款已生效")
            if case.status == CaseStatus.FAILED.value:
                return RefundResult(False, "案件历史退款失败，未再次发起")

            last_msg = "未知错误"
            for attempt in range(1, self._max_attempts + 1):
                # 首试沿用调用方 refund_key（保持与既有幂等/测试约定一致）；
                # 重试用派生子键（build_key 保持 64 位），避免失败结果被单一键缓存冻结
                per_key = refund_key if attempt == 1 else build_key(refund_key, str(attempt))

                def _try_once():
                    return self._attempt_once(db, case_id, amount_cent, per_key, attempt)

                first, res = execute_idempotent(
                    per_key,
                    f"refund:{case_id}:{amount_cent}",
                    _try_once,
                )
                if not first:
                    # 同键重入（LangGraph/Worker 重放）：返回首次已缓存结果，防重复退款
                    val = res or {"success": False, "message": "退款处理中"}
                    return RefundResult(bool(val.get("success")), val.get("message", ""))
                if res.get("success"):
                    return RefundResult(True, res.get("message", "退款成功"))
                last_msg = res.get("message", "退款失败")
                logger.warning(
                    "case %s 退款第 %s/%s 次失败: %s",
                    case_id, attempt, self._max_attempts, last_msg,
                )
            # 有界重试耗尽：REFUND_FAILED -> FAILED 终态
            self._sink_failed(db, case_id)
            return RefundResult(False, f"退款失败（重试 {self._max_attempts} 次）: {last_msg}")
        finally:
            db.close()

    def _attempt_once(self, db, case_id: int, amount_cent: int, refund_key: str, attempt: int) -> dict:
        case = db.get(RefundCase, case_id)
        if case is None:
            return {"success": False, "message": "案件不存在"}
        if case.status not in (CaseStatus.APPROVED.value, CaseStatus.REFUND_FAILED.value):
            # 非可退款态（如并发已转走/终态）：幂等防重复
            return {"success": False, "message": f"状态 {case.status} 不可发起退款"}

        # APPROVED/REFUND_FAILED -> REFUNDING（进入本尝试）
        if not self._transition(db, case, CaseStatus.REFUNDING.value):
            return {"success": False, "message": "状态已变更，本次未执行"}
        case = db.get(RefundCase, case_id)  # 刷新乐观锁版本

        if self._gateway(attempt):
            # REFUNDING -> COMPLETED
            if not self._transition(db, case, CaseStatus.COMPLETED.value):
                return {"success": False, "message": "状态已变更，本次未执行"}
            db.add(
                RefundRecord(
                    case_id=case_id, amount=amount_cent, status="SUCCESS",
                    retry_count=attempt - 1, idempotency_key=refund_key,
                )
            )
            db.add(
                AuditLog(
                    case_id=case_id,
                    from_status=CaseStatus.REFUNDING.value,
                    to_status=CaseStatus.COMPLETED.value,
                    operator="refund-worker",
                    idempotency_key=refund_key,
                )
            )
            db.commit()
            publish_event(case_id, "STATUS_CHANGED", {"status": CaseStatus.COMPLETED.value})
            logger.info("case %s Mock 退款成功 %.2f 元（第 %s 次尝试）", case_id, amount_cent / 100, attempt)
            return {"success": True, "message": "退款成功（Mock）"}

        # 网关失败：REFUNDING -> REFUND_FAILED（可重试，等待下一轮）
        if not self._transition(db, case, CaseStatus.REFUND_FAILED.value):
            return {"success": False, "message": "状态已变更，本次未执行"}
        db.add(
            RefundRecord(
                case_id=case_id, amount=amount_cent, status="FAILED",
                retry_count=attempt - 1, idempotency_key=refund_key,
            )
        )
        db.add(
            AuditLog(
                case_id=case_id,
                from_status=CaseStatus.REFUNDING.value,
                to_status=CaseStatus.REFUND_FAILED.value,
                operator="refund-worker",
                idempotency_key=refund_key,
            )
        )
        db.commit()
        publish_event(case_id, "STATUS_CHANGED", {"status": CaseStatus.REFUND_FAILED.value})
        return {"success": False, "message": f"网关失败（第 {attempt} 次尝试）"}

    @staticmethod
    def _transition(db, case, target: str) -> bool:
        """乐观锁推进案件状态；返回是否成功（0 行 = 并发冲突/已被改）。"""
        r = db.execute(
            update(RefundCase)
            .where(RefundCase.id == case.id, RefundCase.version == case.version)
            .values(status=target, version=case.version + 1)
        )
        return r.rowcount == 1

    def _sink_failed(self, db, case_id: int) -> None:
        """重试耗尽：REFUND_FAILED -> FAILED 终态（仅当仍处于 REFUND_FAILED）。"""
        case = db.get(RefundCase, case_id)
        if case is None or case.status != CaseStatus.REFUND_FAILED.value:
            return
        if self._transition(db, case, CaseStatus.FAILED.value):
            db.add(
                AuditLog(
                    case_id=case_id,
                    from_status=CaseStatus.REFUND_FAILED.value,
                    to_status=CaseStatus.FAILED.value,
                    operator="refund-worker",
                )
            )
            db.commit()
            publish_event(case_id, "STATUS_CHANGED", {"status": CaseStatus.FAILED.value})