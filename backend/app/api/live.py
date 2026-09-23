"""数字人直播接口：开播 / 停播 / 状态 / 弹幕互动 / 讲解控制（买家端「数字人直播」页）。

链路：观众弹幕 → agicto deepseek 生成带货话术 → 算力云渲染服务（CosyVoice TTS +
      MuseTalk 口型）→ mp4 → 前端 <video> 播放。
自动讲解：7 段直播 SOP（脚本骨架）+ LLM 按段改写，按渲染视频的时长推进节奏；
          弹幕问答抢占讲解，问答视频播完自动续讲。

设计取舍：
- 单个进程只维护一路直播会话，因此不开鉴权细分，
  任何买家都能看到同一场直播——这与真实电商「一个直播间多观众」的模型一致。
- 开播/停播不限定角色：演示环境里由买家端页面自行控制（真实投产应加运营鉴权）。
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.domain.models import Product
from app.live import dialogue
from app.live.renderer import RenderError, RenderNotConfigured
from app.live.service import manager, opening_line_for

logger = logging.getLogger("live.api")

router = APIRouter(prefix="/api/v1/live", tags=["live"])


class StartRequest(BaseModel):
    product_id: int | None = Field(default=None, description="讲解商品（products.product_id）")
    persona: str = Field(default="", description="主播人设，留空用默认")
    opening: bool = Field(default=True, description="是否开播后自动说开场白")
    touting: bool = Field(default=True, description="是否开播后自动循环讲解商品")


class DanmuRequest(BaseModel):
    question: str = Field(min_length=1, max_length=200)


class ToutingRequest(BaseModel):
    enabled: bool = Field(description="true=继续自动讲解；false=暂停（弹幕互动不受影响）")


def _decode_config_error(exc: Exception) -> HTTPException:
    """把配置缺失与接口失败区分开：前者 400（改配置即可），后者 502（上游问题）。"""
    if isinstance(exc, RenderNotConfigured):
        return HTTPException(
            status_code=400,
            detail={
                "code": "LIVE_NOT_CONFIGURED",
                "message": f"{exc}。请在 .env 配置 DH_API_BASE_URL（算力云渲染服务地址）",
            },
        )
    return HTTPException(
        status_code=502, detail={"code": "LIVE_UPSTREAM_ERROR", "message": str(exc)}
    )


@router.get("/status")
def live_status():
    """当前直播状态（前端每 3 秒轮询：未开播显示占位，开播挂播放器并显示讲解进度）。"""
    return manager.status()


@router.post("/start")
async def live_start(
    body: StartRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """开播：登记商品/人设/话术脚本 → 渲染开场白 → 启动自动讲解。"""
    product = None
    if body.product_id is not None:
        product = db.query(Product).filter_by(product_id=body.product_id).first()

    product_context = dialogue.build_product_context(product)
    opening = opening_line_for(product.name if product else "") if body.opening else ""
    try:
        return await manager.start(
            product_context=product_context,
            product_name=product.name if product else "",
            product_desc=product.description if product else "",
            product_price=f"{product.price_cents / 100:.2f}" if product else "",
            persona=body.persona,
            opening_line=opening,
            touting=body.touting,
        )
    except (RenderNotConfigured, RenderError) as e:
        logger.warning("开播失败: %s", e)
        raise _decode_config_error(e) from e


@router.post("/stop")
async def live_stop():
    """停播：停讲解循环、清排队任务。"""
    return await manager.stop()


@router.post("/danmu")
async def live_danmu(body: DanmuRequest):
    """弹幕互动：生成话术（文字立即回显）并排队渲染，数字人开口抢占讲解。"""
    try:
        return await manager.danmu(body.question.strip())
    except RenderError as e:
        raise HTTPException(
            status_code=409, detail={"code": "LIVE_NOT_RUNNING", "message": str(e)}
        ) from e


@router.post("/touting")
async def live_touting(body: ToutingRequest):
    """暂停/继续自动讲解（直播会话不受影响，随时可恢复）。"""
    if not manager.running:
        raise HTTPException(
            status_code=409,
            detail={"code": "LIVE_NOT_RUNNING", "message": "直播未开播，无法控制讲解"},
        )
    return manager.set_touting(body.enabled)
