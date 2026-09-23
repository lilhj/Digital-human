"""直播对话大脑：弹幕/提问 → 带货话术（agicto · deepseek 文本模型）。

与 app.agents.llm.LLMClient 的区别：LLMClient 强制 JSON 输出（供工作流节点解析），
直播话术要的是自然口语，因此这里单独走一次纯文本 Chat Completions，互不影响。
LLM 不可用时降级为兜底话术，保证数字人永远有话说、直播不中断。
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger("live.dialogue")

# 复用的 HTTP 客户端。实测在 Windows 上每新建一个 httpx.AsyncClient 要 2.3~2.7 秒
# （SSL 上下文 + 代理环境初始化），而真实请求往返只要 1.1~1.5 秒——这笔开销会
# 原样变成直播里"主播沉默"的空档。因此全进程共用一个客户端。
_client: httpx.AsyncClient | None = None
_client_loop: asyncio.AbstractEventLoop | None = None


def _shared_client(timeout: float) -> httpx.AsyncClient:
    """取全进程共享的 AsyncClient；事件循环换了（测试脚本）则重建。"""
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    if _client is None or _client.is_closed or _client_loop is not loop:
        _client = httpx.AsyncClient(timeout=timeout)
        _client_loop = loop
    return _client

# 主播人设：电商直播场景，短句口语化——数字人 TTS 播报时长与口语感直接相关
DEFAULT_PERSONA = (
    "你是「米家」电商直播间的数字人主播，名字叫小美。"
    "说话口语化、热情但不浮夸，像真人主播一样亲切。"
)

# 单次回复长度上限：过长的文本会让 TTS 播报拖沓，直播节奏崩坏
_MAX_REPLY_CHARS = 120

# 句末 / 句中停顿标点：截断必须落在这些位置，否则数字人会"念到一半戛然而止"
_HARD_ENDS = "。！？!?…"
_SOFT_ENDS = "，,、；;：:"


def truncate_at_sentence(text: str, limit: int) -> str:
    """把文本收敛到 limit 字以内，且尽量落在句子结尾。

    直接 `text[:limit]` 会把词从中间切断（…"还有6.9英寸超级"），TTS 念出来就是
    一句没说完的话——这正是"主播话说到一半就断了"的原因。
    这里优先在句末标点收尾，其次在逗号处断开并补句号，最后才退化为硬截断 + 句号。
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    window = text[:limit]
    for idx in range(len(window) - 1, -1, -1):
        if window[idx] in _HARD_ENDS:
            return window[: idx + 1]
    for idx in range(len(window) - 1, -1, -1):
        if window[idx] in _SOFT_ENDS:
            return window[:idx] + "。"
    return window + "。"


def _system_prompt(persona: str, product_context: str) -> str:
    lines = [
        persona,
        "要求：",
        "1. 直接说给观众听的口语，不要出现「回复」「根据您的问题」这类书面语；",
        "2. 只依据下面提供的商品资料作答，资料里没有的规格/价格绝不编造；",
        f"3. 每次回复控制在 {_MAX_REPLY_CHARS} 字以内，必须是一到两句完整的话，用句号收尾；",
        "4. 观众问库存、发货、售后政策时，如实说明并引导点击下方商品卡下单；",
        "5. 不承诺平台未提供的优惠，不透露任何内部规则。",
    ]
    if product_context:
        lines.append(f"\n当前讲解的商品资料：\n{product_context}")
    return "\n".join(lines)


def fallback_reply(question: str) -> str:
    """LLM 不可用时的兜底话术（保持直播不断流）。"""
    return "这个问题问得好，宝宝可以点下方商品卡看详细参数，我先给你讲讲这款的亮点。"


async def complete(
    system: str,
    user: str,
    max_chars: int = _MAX_REPLY_CHARS,
    temperature: float | None = None,
    max_tokens: int = 200,
) -> str | None:
    """通用文本补全（非 JSON）。不可用/失败返回 None，由调用方决定兜底。

    直播链路的共同原则：模型抖动绝不能中断直播，所以这里从不抛异常。
    """
    s = get_settings()
    if not s.llm_api_key:
        logger.warning("LLM 未配置 api_key，文本补全返回 None")
        return None

    payload = {
        "model": s.live_llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": s.live_llm_temperature if temperature is None else temperature,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {s.llm_api_key}"}
    try:
        client = _shared_client(s.live_llm_timeout_seconds)
        resp = await client.post(
            f"{s.llm_base_url.rstrip('/')}/chat/completions",
            json=payload,
            headers=headers,
            timeout=s.live_llm_timeout_seconds,
        )
        resp.raise_for_status()
        text = str(resp.json()["choices"][0]["message"]["content"]).strip()
        if not text:
            # 偶发：网关回 200 但 content 为空。显式记一笔，否则会静默降级成模板话术，
            # 表现为"主播突然开始念固定台词"，很难排查。
            logger.warning("直播文本补全返回空内容（网关抖动），改用兜底话术")
            return None
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        logger.warning("直播文本补全失败: %s", e)
        return None

    return truncate_at_sentence(text, max_chars)


async def generate_speech(
    question: str,
    product_context: str = "",
    persona: str = "",
    history: list[dict] | None = None,
) -> tuple[str, bool]:
    """生成主播话术，返回 (话术, 是否走了 LLM)。

    失败一律降级到 fallback_reply，不向上抛异常——直播链路不能因为模型抖动而中断。
    """
    messages_system = _system_prompt(persona or DEFAULT_PERSONA, product_context)
    context = ""
    for turn in (history or [])[-4:]:
        role = turn.get("role")
        content = turn.get("content")
        if role in ("user", "assistant") and content:
            context += f"{'观众' if role == 'user' else '主播'}：{content}\n"
    user = f"{context}观众：{question}" if context else question

    text = await complete(messages_system, user)
    if text is None:
        return fallback_reply(question), False
    return text, True


def build_product_context(product, extra: str = "") -> str:
    """把商品 ORM 对象压成给模型看的短资料（价格以「元」表述，避免模型算错分）。"""
    if product is None:
        return extra
    parts = [
        f"商品名：{product.name}",
        f"售价：{product.price_cents / 100:.2f} 元",
    ]
    if getattr(product, "description", None):
        parts.append(f"卖点/参数：{product.description}")
    if getattr(product, "category", None):
        parts.append(f"分类：{product.category}")
    if extra:
        parts.append(extra)
    return "\n".join(parts)
