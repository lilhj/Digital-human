"""算力云数字人渲染出口：话术文本 → AutoDL /say → 可播放的 mp4 地址。

链路：本服务（渲染编排） vs 腾讯 IVH 时代的差别只在最后一跳——
  IVH：文本 → 云端 SEND_TEXT → 腾讯 TTS+渲染 → webrtc 流
  现在：文本 → 算力云 /say → CosyVoice2 TTS → MuseTalk 口型 → mp4（HTTP 轮询取流）

设计原则与 dialogue.py 一致：渲染耗时约 1~2 分钟/句，**复用全进程共享的
httpx.AsyncClient**（Windows 上新建 client 要 2.3~2.7 秒，不能每句新建一个）。
"""
from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger("live.renderer")


class RenderError(Exception):
    """渲染服务调用失败（网络/上游 5xx）。"""


class RenderNotConfigured(Exception):
    """未配置渲染服务地址（.env 缺 DH_API_BASE_URL）。"""


_client: httpx.AsyncClient | None = None


def _shared_client(timeout: float) -> httpx.AsyncClient:
    """全进程共享的 AsyncClient；连接被远端断开时重建。"""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=timeout)
    return _client


async def render_speech(text: str) -> dict:
    """把一段话术渲染成数字人视频。

    返回 {"video_url": "/videos/say_xxx.mp4", "duration": 12.3, "elapsed": 78.0}。
    失败抛 RenderError / RenderNotConfigured，由调用方决定兜底。
    """
    s = get_settings()
    if not s.dh_api_base_url:
        raise RenderNotConfigured("未配置 DH_API_BASE_URL（算力云渲染服务地址）")

    client = _shared_client(s.dh_api_timeout_seconds)
    try:
        resp = await client.post(
            f"{s.dh_api_base_url.rstrip('/')}/say",
            json={"text": text},
            timeout=s.dh_api_timeout_seconds,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as e:
        logger.warning("渲染服务调用失败: %s", e)
        raise RenderError(f"渲染服务调用失败: {e}") from e

    video_url = data.get("video_url")
    if not video_url:
        raise RenderError("渲染服务返回缺少 video_url")

    # 相对地址补全成可被浏览器直接访问的完整 URL（前端 <video> 直连渲染服务）
    base = s.dh_api_base_url.rstrip("/")
    absolute = video_url if str(video_url).startswith("http") else base + video_url
    return {
        "video_url": absolute,
        "duration": float(data.get("duration") or 0.0),
        "elapsed": float(data.get("elapsed") or 0.0),
    }
