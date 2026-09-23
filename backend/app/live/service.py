"""直播会话管理器：算力云数字人（文本→TTS→口型→mp4）+ 自动讲解调度 + 弹幕抢占。

状态机：idle → live（渲染服务不可用时报 failed，配置修复后可重新开播）

与腾讯 IVH 时代的差别只在"最后一跳"：
  IVH ：文本 → SEND_TEXT → 腾讯云端 TTS+渲染 → webrtc 流（秒级）
  现在：文本 → 算力云 /say → CosyVoice2 + MuseTalk → mp4（分钟级，同步渲染）

**节拍器**：/say 返回时视频已经渲好，并附带音频时长 duration。观众"看完这段
视频"的时间 = duration + 缓冲，调度循环据此推进下一段，不再依赖云端回调。

**弹幕抢占**：弹幕问答同样走渲染；渲染请求串行排队（GPU 一次只能渲一条），
问答排在队首，讲解挂起（`_qa_active > 0`）到问答视频开播并放完再续讲。

单例设计：一个进程只维护"当前这一路直播"。
"""
from __future__ import annotations

import asyncio
import logging
import random
import time

from app.core.config import get_settings
from app.live import dialogue, renderer
from app.live.renderer import RenderError, RenderNotConfigured
from app.live.script import ToutingScript

logger = logging.getLogger("live.session")

# 渲染出视频后，观众看完这段视频的额外缓冲（秒）
VIEW_BUFFER = 2.0
# 渲染失败时的兜底"观看时长"，避免讲解循环卡死
FALLBACK_VIEW_SECONDS = 20.0


class LiveSessionManager:
    """单路直播会话管理器（进程内单例）。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._touting_task: asyncio.Task | None = None

        self.state = "idle"          # idle / live / failed
        self.error: str | None = None
        self.started_at: float | None = None
        self.product_context: str = ""
        self.product_name: str = ""
        self.persona: str = ""
        self.history: list[dict] = []
        self.turns: int = 0

        # 播报状态：最新渲染出的视频 + "观众应看到什么时候"
        self.current_video_url: str | None = None
        self.current_line: str = ""
        self._view_until: float = 0.0     # 观众应看到该视频的时间戳（monotonic）
        self.speaking: bool = False

        # 自动讲解
        self.script: ToutingScript | None = None
        self.touting_enabled: bool = False
        self.use_llm: bool = True
        self.gap_seconds: float = 2.5
        self.gap_jitter: float = 0.0      # 停顿抖动幅度：每段在 gap±jitter 内随机，避免节拍器感
        self._pending: asyncio.Task | None = None   # 预生成中的下一段话术
        self.segment_title: str = ""
        self.segment_index: int = 0
        self.segment_total: int = 0
        self.segment_round: int = 1
        self.segments_spoken: int = 0

        # 弹幕抢占计数：>0 时讲解挂起
        self._qa_active: int = 0

    # ---------- 状态 ----------

    @property
    def running(self) -> bool:
        return self.state == "live"

    @property
    def interrupted(self) -> bool:
        return self._qa_active > 0

    def status(self) -> dict:
        s = get_settings()
        idle_url = None
        if s.dh_api_base_url:
            idle_url = s.dh_api_base_url.rstrip("/") + s.dh_idle_video_path
        return {
            "state": self.state,
            "running": self.running,
            "error": self.error,
            "started_at": self.started_at,
            "turns": self.turns,
            "configured": bool(s.dh_api_base_url),
            # 数字人画面：说话时播最新渲染的视频，其余时间循环待机视频
            "current_video_url": self.current_video_url,
            "idle_video_url": idle_url,
            "speaking": self.speaking,
            # 讲解进度（前端据此显示"正在讲：核心卖点"）
            "touting_enabled": self.touting_enabled,
            "segment": self.segment_title,
            "segment_index": self.segment_index,
            "segment_total": self.segment_total,
            "segment_round": self.segment_round,
            "segments_spoken": self.segments_spoken,
            "interrupted": self.interrupted,
            "current_line": self.current_line,
            "product_name": self.product_name,
        }

    # ---------- 生命周期 ----------

    async def start(
        self,
        product_context: str = "",
        product_name: str = "",
        product_desc: str = "",
        product_price: str = "",
        persona: str = "",
        opening_line: str = "",
        touting: bool = True,
    ) -> dict:
        """开播：登记人设/商品/话术脚本 → 渲染开场白 → 启动自动讲解。已开播则返回当前状态。"""
        async with self._lock:
            if self.running:
                return self.status()

            s = get_settings()
            if not s.dh_api_base_url:
                self.state = "failed"
                self.error = "未配置 DH_API_BASE_URL"
                raise RenderNotConfigured(self.error)

            self.error = None
            self.product_context = product_context
            self.product_name = product_name
            self.persona = persona or dialogue.DEFAULT_PERSONA
            self.history = []
            self.turns = 0
            self.state = "live"
            self.started_at = time.time()
            self.current_video_url = None
            self._view_until = 0.0
            self.speaking = False
            self._reset_script(product_context, product_name, product_desc, product_price)
            self.gap_seconds = s.live_touting_gap_seconds
            self.gap_jitter = max(0.0, s.live_touting_gap_jitter)
            self.use_llm = s.live_touting_use_llm

            if opening_line:
                self.history.append({"role": "assistant", "content": opening_line})
                # 开场白已经打过招呼了，讲解循环直接从"核心卖点"接上，
                # 否则第一段「开场」会把同样的招呼再讲一遍（观众听得出重复）。
                if self.script is not None and self.script.index == 0:
                    self.script.advance()
                self._qa_active += 1
                asyncio.create_task(self._render_and_settle(opening_line))

            self.touting_enabled = bool(touting) and s.live_touting_enabled
            if self.touting_enabled:
                # 开场白在渲染，现在就把第一段想好，避免"打完招呼后干等"
                self._kick_prefetch()
                self._ensure_touting_task()
            logger.info("直播已开播（算力云渲染）product=%s", self.product_name)
            return self.status()

    def _reset_script(
        self,
        product_context: str,
        product_name: str,
        product_desc: str,
        product_price: str,
    ) -> None:
        self.script = ToutingScript(
            product_context=product_context,
            product_name=product_name,
            desc=product_desc,
            price=product_price,
        )
        self.segment_total = self.script.total
        self.segment_index = 0
        self.segment_title = ""
        self.segment_round = 1
        self.segments_spoken = 0
        self.touting_enabled = False
        self._qa_active = 0
        self._drop_prefetch()

    # ---------- 渲染出口（替代 IVH 的 SEND_TEXT） ----------

    async def _speak(self, text: str) -> dict:
        """把一段话术送到算力云渲染成视频；完成后更新当前播放地址。

        /say 是同步接口：返回时视频已渲好，耗时约 1~2 分钟。
        """
        self.current_line = text
        info = await renderer.render_speech(text)
        self.current_video_url = info["video_url"]
        self.speaking = True
        self._view_until = time.monotonic() + info["duration"] + VIEW_BUFFER
        return info

    async def _render_and_settle(self, text: str) -> None:
        """渲染一段话术，并按视频时长放行讲解循环（弹幕问答/开场白共用）。"""
        try:
            info = await self._speak(text)
            logger.info("话术渲染完成 (%.1fs 渲染 / %.1fs 视频)", info["elapsed"], info["duration"])
            await asyncio.sleep(info["duration"] + VIEW_BUFFER)
        except RenderError as e:
            logger.warning("话术渲染失败（%.0fs 后继续）: %s", FALLBACK_VIEW_SECONDS, e)
            await asyncio.sleep(FALLBACK_VIEW_SECONDS)
        except asyncio.CancelledError:
            raise
        finally:
            self._qa_active = max(0, self._qa_active - 1)
            if self._qa_active == 0:
                self.speaking = False

    async def _wait_view_done(self) -> None:
        """等观众看完当前这段视频（到 _view_until 为止），空闲时立即通过。"""
        wait = self._view_until - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)

    # ---------- 自动讲解调度 ----------

    def _ensure_touting_task(self) -> None:
        if self._touting_task is None or self._touting_task.done():
            self._touting_task = asyncio.create_task(self._touting_loop())

    def _breath_seconds(self) -> float:
        """这一段的换气停顿时长。

        真人主播不会每次都停顿一样久——说完一段长卖点会多喘一口气，短促的促单
        句几乎不停。固定间隔循环几轮后听起来像节拍器，所以在 gap 附近做均匀抖动：
        gap=2.0 / jitter=0.4 → 每段随机落在 1.6~2.4 秒。
        jitter=0 时退化为固定值（旧行为）。
        """
        if self.gap_jitter <= 0:
            return self.gap_seconds
        low = max(0.2, self.gap_seconds - self.gap_jitter)   # 不低于 0.2s，免得听成连读
        return random.uniform(low, self.gap_seconds + self.gap_jitter)

    async def _touting_loop(self) -> None:
        """讲解调度循环：等观众看完 → 停顿换气 → 取预生成话术 → 渲染下发 → 推进段落。"""
        await asyncio.sleep(1.5)   # 让开场白先排队
        while self.state == "live":
            if self.script is None or not self.touting_enabled or self._qa_active > 0:
                await asyncio.sleep(0.3)
                continue

            # 1. 等观众看完上一段（空闲时立即通过）
            await self._wait_view_done()
            # 2. 换气停顿，模仿真人主播节奏（每段长短不一，避免节拍器式的机械感）
            await asyncio.sleep(self._breath_seconds())

            # 3. 停顿期间若被弹幕抢占，让位给问答。预生成的话术先留着——
            #    脚本没推进，它对应的仍然是接下来要讲的那一段，内容依然有效。
            if self._qa_active > 0 or not self.touting_enabled or self.state != "live":
                continue

            # 4. 优先用预生成好的话术（LLM 的 1~2 秒已经藏在上一段的渲染时间里）；
            #    首段、被弹幕打断过或预生成失败时，退回现场生成。
            got = await self._take_prefetch()
            if got is None:
                try:
                    got = await self.script.next_line(self.use_llm)
                except Exception as e:
                    logger.warning("生成讲解段落失败: %s", e)
                    await asyncio.sleep(1.0)
                    continue
            line, seg, _used_llm = got

            if self._qa_active > 0 or self.state != "live":
                continue

            # 5. 渲染下发并推进段落（渲染 1~2 分钟是同步等待；弹幕文字回复不受影响）
            try:
                info = await self._speak(line)
            except RenderError as e:
                logger.warning("讲解段落渲染失败，%.0fs 后重试: %s", FALLBACK_VIEW_SECONDS, e)
                await asyncio.sleep(FALLBACK_VIEW_SECONDS)
                continue
            except Exception as e:
                logger.warning("讲解段落下发异常，讲解循环退出: %s", e)
                return

            self.segment_title = seg.get("title", "")
            self.segment_index = self.script.index
            self.segment_round = self.script.round
            self.segments_spoken += 1
            self.history.append({"role": "assistant", "content": line})
            self.script.advance()
            # 段落已推进，趁观众看这段视频的时间把下一段先想好
            self._kick_prefetch()
            logger.info(
                "讲解第 %d 段已渲染 (%.1fs 视频, 累计 %s 段)",
                self.segment_index + 1, info["duration"], self.segments_spoken,
            )

    def _kick_prefetch(self) -> None:
        """提前生成下一段话术。

        生成话术那 1~2 秒没必要干等着——主播正在"播"当前这段，正是拿来
        "想下一句"的时间。前提是脚本已经 advance()，否则生成出来还是当前这一段。
        """
        if self.script is None or not self.use_llm:
            return
        if self._pending is not None and not self._pending.done():
            return
        self._pending = asyncio.create_task(self.script.next_line(True))

    async def _take_prefetch(self) -> tuple[str, dict[str, str], bool] | None:
        """取回预生成的话术；没有或失败返回 None（调用方改为现场生成）。"""
        task, self._pending = self._pending, None
        if task is None:
            return None
        try:
            return await task
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("预生成话术失败，改为现场生成: %s", e)
            return None

    def _drop_prefetch(self) -> None:
        """丢弃预生成任务（停播/重开时调用，避免残留任务白烧 token）。"""
        if self._pending is not None:
            self._pending.cancel()
            self._pending = None

    def set_touting(self, enabled: bool) -> dict:
        """暂停/继续自动讲解（不影响已开播的会话）。"""
        self.touting_enabled = bool(enabled)
        if self.touting_enabled and self.state == "live":
            self._ensure_touting_task()
        return self.status()

    # ---------- 弹幕互动 ----------

    async def danmu(self, question: str) -> dict:
        """一条弹幕/提问：LLM 生成话术 → 文字立即回显 → 渲染排队抢占讲解。"""
        if self.state != "live":
            raise RenderError("直播未开播，无法互动")

        reply, used_llm = await dialogue.generate_speech(
            question,
            product_context=self.product_context,
            persona=self.persona,
            history=self.history,
        )
        self.history.append({"role": "user", "content": question})
        self.turns += 1

        # 抢占：计数 >0 时讲解调度挂起，保证主播先回答观众；
        # 渲染在后台任务里排队（GPU 串行），完成后数字人开口、放完自动续讲。
        self._qa_active += 1
        asyncio.create_task(self._render_and_settle(reply))
        return {"reply": reply, "spoken": True, "used_llm": used_llm, "interrupted": True}

    # ---------- 收尾 ----------

    async def stop(self) -> dict:
        """停播：停讲解循环、清排队任务、复位状态。"""
        async with self._lock:
            await self._teardown()
            return self.status()

    async def _teardown(self) -> None:
        self.touting_enabled = False
        self._drop_prefetch()
        if self._touting_task:
            self._touting_task.cancel()
        self._touting_task = None
        self.state = "idle"
        self.started_at = None
        self.history = []
        self.current_video_url = None
        self.current_line = ""
        self.segment_title = ""
        self.speaking = False
        self._view_until = 0.0
        self._qa_active = 0


# 进程内单例（uvicorn 单 worker 下即全局唯一；多 worker 需换 Redis 协调，见 README）
manager = LiveSessionManager()


def opening_line_for(product_name: str = "") -> str:
    """开场白：开播第一句，避免观众进来看到静止画面。"""
    if product_name:
        return f"欢迎来到米家直播间！我是主播小美，今天咱们重点聊{product_name}，有问题随时打在弹幕上。"
    return "欢迎来到米家直播间！我是主播小美，有任何问题都可以打在弹幕上。"


__all__ = ["LiveSessionManager", "manager", "opening_line_for"]
