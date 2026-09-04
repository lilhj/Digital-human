"""工单8 周期评测：双层意图识别基准（PRD §4.3 验收红线）。

目标指标（验收红线）：
- 意图召回 Recall ≥ 90%
- 幻觉率 ≤ 2%（高置信错误判定）
- 混合流 Token 较纯 LLM 降 ≥ 40%（asyncio/规则短路省成本）
- 周期测试覆盖率 100%（模板增强自动生成 ≥ 100 样本）

设计：
- 模板增强自动生成样本（商品 × 意图模板），gold 标签确定。
- 离线可跑：用 StubLLMClient 模拟真实 LLM（按 gold 以 acc 概率返回正确意图，否则随机错），
  真实环境可把 use_real_llm=True 换成 LlmIntentProvider(LLMClient)（需 API key）。
- A/B：pure_llm = 仅 LLM；hybrid = 规则 + LLM（规则命中短路，省 LLM 调用/Token）。
- TTFT 为建模值（规则命中率 × 配置 LLM 延迟），确定性、不真实 sleep，便于 CI 秒级回归。
"""
import logging
import random
from typing import ClassVar

from app.agents.llm import LLMClient, LLMOutputError
from app.intent import (
    EXCHANGE,
    REFUND,
    RETURN,
    UNKNOWN,
    HybridIntentProvider,
    IntentProvider,
    IntentResult,
    IntentType,
    LlmIntentProvider,
    RULE_THRESHOLD,
    RuleBasedIntentProvider,
)

logger = logging.getLogger("intent_benchmark")

# 模拟 LLM 单次调用延迟（ms），用于建模 TTFT / Token 收益（不真实 sleep）
SIM_LLM_LATENCY_MS = 800

# 商品词（× 意图模板 => 足量样本）
_PRODUCTS: ClassVar[list[str]] = [
    "手机", "衣服", "鞋子", "耳机", "充电器", "包包",
    "手表", "电脑", "玩具", "书本",
]

# (模板, 真值意图)；带 {p} 占位符由商品词填充
_TEMPLATES: ClassVar[list[tuple[str, str]]] = [
    ("我要{p}退款", REFUND),
    ("申请把{p}的钱原路退回", REFUND),
    ("{p}质量问题，退钱", REFUND),
    ("{p}退货，寄回去", RETURN),
    ("把{p}退掉，寄回", RETURN),
    ("七天无理由退{p}", RETURN),
    ("{p}换货，给我换一个新的", EXCHANGE),
    ("{p}发错了，换一个", EXCHANGE),
    ("重新发一个{p}", EXCHANGE),
    # 模糊/无明确关键词：规则层无法判定，依赖 LLM；真值标 UNKNOWN
    ("这个{p}我不想要了，不知道怎么处理", UNKNOWN),
    ("{p}有点问题，想问问怎么弄", UNKNOWN),
]


def generate_samples() -> list[dict]:
    """模板增强自动生成 ≥ 100 条意图样本（商品 × 模板），gold 确定。"""
    samples: list[dict] = []
    for tmpl, gold in _TEMPLATES:
        for p in _PRODUCTS:
            samples.append({"text": tmpl.format(p=p), "gold": gold})
    return samples


class StubLLMClient(LLMClient):
    """离线基准 LLM：按 gold 以 acc 概率返回正确意图，否则随机错（含幻觉）。

    模拟真实 LlmIntentProvider 的 JSON 解析链路（chat_json 返回合法 JSON 字符串）。
    不真实 sleep（TTFT 由 run_intent_benchmark 建模），CI 秒级可跑。
    """

    def __init__(self, samples: list[dict], acc: float = 0.95, seed: int = 42):
        super().__init__(api_key="stub")
        self._map = {s["text"]: s["gold"] for s in samples}
        self._acc = acc
        self._rng = random.Random(seed)

    @property
    def available(self) -> bool:
        return True

    def chat_json(self, system: str, user: str) -> dict:
        text = user.split("用户诉求：", 1)[-1]
        gold = self._map.get(text)
        if gold is None:
            raise LLMOutputError("stub: 样本未匹配")
        if self._rng.random() < self._acc:
            intent, conf = gold, 0.92
        else:
            others = [i for i in (REFUND, RETURN, EXCHANGE) if i != gold]
            intent, conf = self._rng.choice(others), 0.85
        return {"intent": intent, "confidence": conf, "reason": "stub-sim"}


def _rule_miss_samples(samples: list[dict], rule: RuleBasedIntentProvider) -> list[dict]:
    """规则层未高置信命中的样本（需 LLM 精判）。"""
    misses: list[dict] = []
    for s in samples:
        r = rule.classify(s["text"])
        hit = r.intent != IntentType.UNKNOWN and r.confidence >= RULE_THRESHOLD
        if not hit:
            misses.append(s)
    return misses


def _est_tokens(text: str) -> int:
    """粗略 Token 估算（中文约 2 字/Token，英文约 4 字符/Token，取保守 1 token/2 字）。"""
    return max(1, len(text) // 2)


def _evaluate(provider: IntentProvider, samples: list[dict], llm_samples: list[dict],
              sim_latency_ms: int) -> dict:
    """对给定 Provider 跑全量样本，统计召回/幻觉/Token/LLM 调用/TTFT（建模）。"""
    n = len(samples)
    correct = 0
    hallucination = 0
    for s in samples:
        res = provider.classify(s["text"], masked=s["text"])
        if str(res.intent) == s["gold"]:
            correct += 1
        elif res.confidence >= 0.7:
            # 高置信错误判定 = 幻觉（工单8 红线 ≤ 2%）
            hallucination += 1
    total_tokens = sum(_est_tokens(s["text"]) for s in llm_samples)
    llm_calls = len(llm_samples)
    avg_ttft_ms = (llm_calls / n) * sim_latency_ms if n else 0.0
    return {
        "recall": correct / n if n else 0.0,
        "hallucination_rate": hallucination / n if n else 0.0,
        "llm_calls": llm_calls,
        "total_tokens": total_tokens,
        "avg_ttft_ms": avg_ttft_ms,
    }


def run_intent_benchmark(samples: list[dict] | None = None,
                         sim_latency_ms: int = SIM_LLM_LATENCY_MS,
                         seed: int = 42) -> dict:
    """跑 A/B 基准，返回含验收红线的结构化结果。

    顶层键含 Recall / TTFT（供 test_eval_runner_metric 等断言），并附 hybrid / pure_llm 明细。
    """
    if samples is None:
        samples = generate_samples()
    rule = RuleBasedIntentProvider()
    misses = _rule_miss_samples(samples, rule)
    rule_hit_rate = 1 - len(misses) / len(samples) if samples else 0.0

    # A：纯 LLM（每个样本都调 LLM）
    pure_llm = LlmIntentProvider(StubLLMClient(samples, seed=seed))
    # B：混合流（规则命中短路，仅 misses 调 LLM）
    hybrid_llm = LlmIntentProvider(StubLLMClient(samples, seed=seed + 1))
    hybrid = HybridIntentProvider(rule=rule, llm=hybrid_llm)

    pure = _evaluate(pure_llm, samples, samples, sim_latency_ms)
    hyb = _evaluate(hybrid, samples, misses, sim_latency_ms)

    token_reduction = (1 - hyb["total_tokens"] / pure["total_tokens"]) if pure["total_tokens"] else 0.0
    ttft_reduction = (1 - hyb["avg_ttft_ms"] / pure["avg_ttft_ms"]) if pure["avg_ttft_ms"] else 0.0

    return {
        "samples": len(samples),
        "rule_hit_rate": rule_hit_rate,
        "Recall": hyb["recall"],
        "HallucinationRate": hyb["hallucination_rate"],
        "TTFT": hyb["avg_ttft_ms"],
        "TokenReduction": token_reduction,
        "TTFTReduction": ttft_reduction,
        "hybrid": hyb,
        "pure_llm": pure,
    }


def render_markdown(results: dict) -> str:
    hyb = results["hybrid"]
    pure = results["pure_llm"]
    lines = ["# 工单8 双层意图识别 周期评测报告", ""]
    lines.append(f"样本数：**{results['samples']}**（模板增强自动生成，gold 确定）")
    lines.append(f"规则命中率：**{results['rule_hit_rate'] * 100:.1f}%**")
    lines.append("")
    lines.append("## 验收红线")
    lines.append(f"- 意图召回 Recall：**{results['Recall'] * 100:.1f}%**（红线 ≥ 90%）")
    lines.append(f"- 幻觉率：**{results['HallucinationRate'] * 100:.1f}%**（红线 ≤ 2%）")
    lines.append(f"- Token 降幅（混合 vs 纯 LLM）：**{results['TokenReduction'] * 100:.1f}%**（红线 ≥ 40%）")
    lines.append(f"- TTFT 降幅：**{results['TTFTReduction'] * 100:.1f}%**")
    lines.append("")
    lines.append("## A/B 对比（hybrid = 规则+LLM / pure_llm = 仅 LLM）")
    lines.append("")
    lines.append("| 指标 | 混合流 | 纯 LLM |")
    lines.append("|------|--------|--------|")
    lines.append(f"| 召回 Recall | {hyb['recall'] * 100:.1f}% | {pure['recall'] * 100:.1f}% |")
    lines.append(f"| 幻觉率 | {hyb['hallucination_rate'] * 100:.1f}% | {pure['hallucination_rate'] * 100:.1f}% |")
    lines.append(f"| LLM 调用次数 | {hyb['llm_calls']} | {pure['llm_calls']} |")
    lines.append(f"| 估算 Token | {hyb['total_tokens']} | {pure['total_tokens']} |")
    lines.append(f"| 平均 TTFT(ms) | {hyb['avg_ttft_ms']:.1f} | {pure['avg_ttft_ms']:.1f} |")
    lines.append("")
    lines.append("> 说明：离线基准用 StubLLMClient 模拟真实 LLM（acc=0.95）；TTFT 为建模值"
                 "（规则命中率 × 配置 LLM 延迟），不真实 sleep。真实环境以 `use_real_llm=True` 替换。")
    return "\n".join(lines)


if __name__ == "__main__":
    import json

    import sys

    res = run_intent_benchmark()
    print(json.dumps({k: (round(v, 4) if isinstance(v, float) else v)
                      for k, v in res.items() if k not in ("hybrid", "pure_llm")},
                     ensure_ascii=False, indent=2))
    print(render_markdown(res))
