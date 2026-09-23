"""商品讲解脚本：7 段直播 SOP 骨架 + LLM 按段改写（换说法、不跑偏）。

设计取舍（与用户确认的方案 A+B）：
- **骨架定死**：讲什么、按什么顺序讲，由下面的 SEGMENTS 固定。这保证合规与节奏，
  也保证「讲完一轮」始终是完整的一次带货逻辑，而不是 LLM 想到哪说到哪。
- **LLM 只做改写**：每段的 `brief` 是改写指令（要讲什么角度），LLM 把同一段换种说法，
  解决"讲第二遍观众听出重复"的问题。LLM 不可用时用它自己那段的 `fallback` 兜底。
- **每段控制在 ~55 字**：按实测中文 TTS 约 5.3 字/秒，一段约 10 秒，节奏接近真人主播。
"""
from __future__ import annotations

import logging

from app.live import dialogue

logger = logging.getLogger("live.script")

# 单段目标字数上限：150 字渲染出的视频约 15~18 秒，盖过一次渲染耗时（约 11 秒），
# 让"播完上一段"与"渲好下一段"形成流水线，观感接近连播。
# 注意不能设得太紧——截断必须落在一句话的结尾（见 dialogue.truncate_at_sentence），
# 否则数字人会念到一半戛然而止。
MAX_SEGMENT_CHARS = 150

# 七段直播 SOP：(键, 段名, 改写指令 brief, 兜底话术模板)
# 模板占位符：{name} 商品名、{desc} 卖点描述、{price} 价格（元）
SEGMENTS: list[dict[str, str]] = [
    {
        "key": "opening",
        "title": "开场",
        "brief": "热情打招呼，报出今天主推的商品名，邀请观众把问题打在弹幕上",
        "fallback": "欢迎来到米家直播间，我是主播小美，今天重点聊的是{name}，有想问的直接打弹幕。",
    },
    {
        "key": "highlight",
        "title": "核心卖点",
        "brief": "挑一两个最硬的卖点讲透，说人话、别念参数表，让人听懂它强在哪",
        "fallback": "{name} 的硬货在这儿：{desc}，日常用完全够。",
    },
    {
        "key": "scenario",
        "title": "使用场景",
        "brief": "讲清楚什么人适合买、什么场景下最爽，帮观众对号入座",
        "fallback": "如果你是重度用机的人，{name} 这种配置基本一天不用惦记充电。",
    },
    {
        "key": "compare",
        "title": "差异化",
        "brief": "说明它和同价位产品的差别在哪，突出一个记忆点，但不贬低其他品牌",
        "fallback": "同价位里 {name} 比较实在的地方，就是配置没怎么缩水。",
    },
    {
        "key": "price",
        "title": "价格",
        "brief": "报出价格，强调性价比，不要编造任何未提供的优惠或赠品",
        "fallback": "{name} 到手价 {price} 元，这个价位段里算挺能打的。",
    },
    {
        "key": "service",
        "title": "售后保障",
        "brief": "说明平台提供正常的退换与售后保障流程，不承诺任何平台未说明的额外权益",
        "fallback": "下单后走平台正常售后流程，有问题随时找客服，退换都按规则来。",
    },
    {
        "key": "cta",
        "title": "促单",
        "brief": "引导点击下方商品卡下单，营造一点紧迫感但不要虚假宣传库存",
        "fallback": "喜欢的话就点下方商品卡看看，先拍先安排发货。",
    },
]

_TONE = (
    "你是电商直播间的数字人主播小美，正在实时口播带货。"
    "要求：口语化、像真人主播说话，不要书面语；"
    f"只输出一段 {MAX_SEGMENT_CHARS} 字以内的口播，必须说完一整句话并用句号结尾；"
    "不要加任何解释、标题、小标题或表情符号；"
    "只依据提供的商品资料，未提供的价格、优惠、库存一律不许编造。"
)


class ToutingScript:
    """一个商品的一轮讲解脚本（持有商品资料，按段推进）。"""

    def __init__(self, product_context: str = "", product_name: str = "", desc: str = "", price: str = "") -> None:
        self.product_context = product_context
        self.name = product_name or "这款商品"
        self.desc = desc or "配置给得很足"
        self.price = price or "以页面显示为准"
        self.index = 0          # 当前讲到第几段
        self.round = 1          # 讲第几轮（轮次用于让 LLM 换说法）

    @property
    def total(self) -> int:
        return len(SEGMENTS)

    def current(self) -> dict[str, str]:
        return SEGMENTS[self.index % self.total]

    def advance(self) -> None:
        """推进到下一段；讲完一轮则回到开场并把轮次 +1（换说法重讲）。"""
        self.index += 1
        if self.index >= self.total:
            self.index = 0
            self.round += 1

    def _fill(self, template: str) -> str:
        return template.format(name=self.name, desc=self.desc, price=self.price)

    def fallback(self) -> str:
        return dialogue.truncate_at_sentence(
            self._fill(self.current()["fallback"]), MAX_SEGMENT_CHARS
        )

    async def next_line(self, use_llm: bool = True) -> tuple[str, dict[str, str], bool]:
        """产出下一段口播，返回 (话术, 段落信息, 是否走了 LLM)。

        调用方负责在播报完成后调用 advance()——顺序推进由调度器控制，
        这里只负责"生成当前段的内容"。
        """
        seg = self.current()
        if not use_llm:
            return dialogue.truncate_at_sentence(self._fill(seg["fallback"]), MAX_SEGMENT_CHARS), seg, False

        user = (
            f"本轮是第 {self.round} 轮讲解，当前要讲的环节是「{seg['title']}」。\n"
            f"这一段的讲解要求：{seg['brief']}\n"
        )
        if self.round > 1:
            user += "注意：已经讲过一遍了，这次请换一种说法，不要和上一轮重复表述。\n"
        if self.product_context:
            user += f"\n商品资料：\n{self.product_context}\n"

        text = await dialogue.complete(_TONE, user, max_chars=MAX_SEGMENT_CHARS)
        if text is None:
            return dialogue.truncate_at_sentence(
                self._fill(seg["fallback"]), MAX_SEGMENT_CHARS
            ), seg, False
        return text, seg, True
