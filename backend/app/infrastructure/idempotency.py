"""幂等服务（裁决 D-014：防资损幂等键公式 + 唯一约束）。

设计公式（Loop 提示词 Phase 7）：
    idempotency_key = SHA256(actor + ":" + case_id + ":" + action + ":" + client_key)

流程：
1. 写前先查：同键已有记录 -> 直接返回首次结果（快速幂等）
2. 首次写入：插入 idempotency_records（request_hash + response_json），
   唯一约束兜底并发（两请求同时插入只有一个成功）
3. IntegrityError 兜底：返回已有结果（并发下的最终一致性）

退款动作同样使用独立幂等键（防 LangGraph 重试触发两次退款）。
"""
import hashlib
import json
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.domain.models import IdempotencyRecord


def build_key(*parts: str) -> str:
    """SHA256 幂等键。parts 如 (actor, case_id, action, client_key)。"""
    raw = ":".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()


def execute_idempotent(
    key: str,
    request_hash: str,
    execute: callable,
) -> tuple[bool, Any]:
    """幂等执行：返回 (是否首次执行, 结果)。

    - 首次：执行 execute() 并保存响应，返回 (True, result)
    - 重复：返回已保存的响应，不重复执行，返回 (False, saved)
    """
    db = SessionLocal()
    try:
        existing = db.query(IdempotencyRecord).filter_by(idempotency_key=key).first()
        if existing is not None:
            return False, existing.response_json

        # 尝试占用（并发下唯一约束保证只有一个成功）
        db.add(
            IdempotencyRecord(
                idempotency_key=key,
                request_hash=request_hash,
                response_json=None,
            )
        )
        try:
            db.commit()
        except IntegrityError:
            # 并发窗口：另一请求已先插入，返回其结果
            db.rollback()
            existing = db.query(IdempotencyRecord).filter_by(idempotency_key=key).first()
            return False, (existing.response_json if existing else None)

        # 已占用：执行业务
        result = execute()

        # 回填响应
        db = SessionLocal()
        try:
            rec = db.query(IdempotencyRecord).filter_by(idempotency_key=key).first()
            if rec is not None:
                rec.response_json = result
                db.commit()
        finally:
            db.close()
        return True, result
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass


def hash_request(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
