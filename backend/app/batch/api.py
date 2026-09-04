"""批量审批接口（需求文档 §3.1）：Dashboard「导出挂起订单 / 上传审批结果」。

- GET  /api/v1/batch/export      导出挂起订单 Excel（只读字段 + 空白审批列）
- POST /api/v1/batch/approve     上传审批结果 Excel -> 沙箱隔离解析 -> 批量审批
- GET  /api/v1/batch/sandbox-mode 查询当前 SANDBOX_MODE（前端开关展示）
"""
import logging
import tempfile
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.batch.service import export_suspended_excel, process_batch_approval
from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import ROLE_MANAGER, get_current_user, require_roles
from app.domain.models import User
from app.infrastructure import streams
from app.infrastructure.streams import (
    get_sandbox_mode_runtime,
    set_sandbox_mode_runtime,
)

router = APIRouter(prefix="/api/v1/batch", tags=["batch"])

logger = logging.getLogger(__name__)

# 沙箱模式枚举（off/on）
_SANDBOX_MODES = ("off", "on")


class SandboxModeBody(BaseModel):
    sandbox_mode: str


def _resolve_sandbox_mode() -> str:
    """按优先级取当前沙箱模式：Redis 运行时开关 -> 配置默认。"""
    runtime = get_sandbox_mode_runtime()
    if runtime in _SANDBOX_MODES:
        return runtime
    return get_settings().sandbox_mode


@router.get("/sandbox-mode")
def get_sandbox_mode(
    user: Annotated[User, Depends(require_roles(ROLE_MANAGER))],
):
    """查询当前生效的沙箱模式（Redis 运行时开关优先；未设置则回落配置默认）。"""
    return {"sandbox_mode": _resolve_sandbox_mode()}


@router.put("/sandbox-mode")
def put_sandbox_mode(
    body: SandboxModeBody,
    user: Annotated[User, Depends(require_roles(ROLE_MANAGER))],
):
    """运行时切换沙箱模式（仅 MANAGER）。off=宿主机直读 / on=真沙箱隔离。

    写入 Redis 全局持久化：立即对所有后续审批生效，无需改 .env / 重启。
    关闭沙箱（on->off）意味着不可信 Excel 转入宿主机解析，属安全降级，
    因此仅在 MANAGER 权限下开放，前端需二次确认。
    """
    mode = (body.sandbox_mode or "").strip().lower()
    if mode not in _SANDBOX_MODES:
        raise HTTPException(
            status_code=422,
            detail={"code": "BAD_SANDBOX_MODE", "message": f"非法沙箱模式: {mode!r}（仅 off/on）"},
        )
    set_sandbox_mode_runtime(mode)
    return {"sandbox_mode": mode, "message": f"沙箱模式已切换为 {mode}"}


@router.get("/export")
def export_suspended(
    user: Annotated[User, Depends(require_roles(ROLE_MANAGER))],
):
    """导出挂起订单 Excel（MANAGER 权限）。

    对异常显式抛出结构化的 500（含真实原因），而不是让异常落到全局兜底
    「服务器内部错误」——否则部署环境少了 openpyxl 等依赖时会变得不可排查。
    """
    try:
        content, filename = export_suspended_excel()
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 - 与 /approve 一致的错误透出
        logger.exception("导出挂起订单 Excel 失败")
        raise HTTPException(
            status_code=500,
            detail={
                "code": "EXPORT_FAILED",
                "message": f"导出失败: {type(e).__name__}: {e}（检查 openpyxl 依赖是否已安装）",
            },
        ) from e
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/approve")
def approve_batch(
    user: Annotated[User, Depends(require_roles(ROLE_MANAGER))],
    file: Annotated[UploadFile, File()],
    sandbox_mode: Annotated[str | None, Form()] = None,
):
    """上传审批结果 Excel -> 解析 -> 批量审批 -> 汇总（MANAGER 权限）。"""
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(
            status_code=400,
            detail={"code": "BAD_FILE", "message": "仅支持 .xlsx 格式"},
        )
    suffix = Path(file.filename).suffix.lower() or ".xlsx"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file.file.read())
        tmp_path = Path(tmp.name)
    try:
        result = process_batch_approval(tmp_path, operator=user.username, sandbox_mode=sandbox_mode)
        return result
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 - 统一错误格式
        raise HTTPException(
            status_code=422,
            detail={"code": "PARSE_FAILED", "message": f"审批 Excel 解析失败: {e}"},
        ) from e
    finally:
        tmp_path.unlink(missing_ok=True)
