"""LLM-as-a-judge 三维评分（需求文档 §3.2：适配退赔决策语义）。

三维：
- correctness 决策正确性：approve/reject/human-review 判得对不对
- safety      金额边界安全：有无误放高风险单 / 误拒低风险单
- efficiency  理由质量：决策依据是否充分可解释
各 1~5 分 + 理由 + 置信度。容错：非字符串/解析失败/越界值全部钳制，不拖垮评测链。

裁判走平台自身的 LLMClient（httpx 直连 agicto.cn OpenAI 兼容接口，与业务节点同一条
出口），默认模型 gpt-4o-mini（config.judge_model）。LLM 调用是同步 httpx，评测为
一次性批处理，用 asyncio.to_thread 包一层避免阻塞事件循环。
"""
import asyncio
import json
import logging

from app.agents.llm import LLMClient
from app.core.config import get_settings

logger = logging.getLogger(__name__)

RUBRIC = {
    "correctness": "1=决策与期望完全不符 3=基本正确但有偏差 5=决策完全符合期望",
    "safety": "1=误放高风险单或误拒低风险单 3=无资损但边界判断有含糊 5=金额/风险边界处理完全安全",
    "efficiency": "1=理由缺失或明显不合理 3=理由基本充分 5=依据充分、可解释、无冗余",
}

_client_factory = None  # 可被测试替换（应返回 LLMClient 实例）


def _client() -> LLMClient:
    if _client_factory is not None:
        return _client_factory()
    return LLMClient(model=get_settings().judge_model)


_JUDGE_PROMPT = """你是退赔决策评测裁判。请按以下 Rubric 对决策结果打分（1~5 分）。

Rubric：
- 决策正确性：{correctness}
- 金额边界安全：{safety}
- 理由质量：{efficiency}

用例：{case}
期望决策：{expected}
系统决策：{agent_result}

只输出 JSON：{{"correctness": int, "safety": int, "efficiency": int, "reason": "一句话理由", "confidence": 0.0~1.0}}"""


def _fallback_score(reason: str) -> dict:
    """裁判失败兜底：全 1 分 + 如实写明原因（不伪装成低分也不算成功）。"""
    return {
        "correctness": 1,
        "safety": 1,
        "efficiency": 1,
        "reason": reason,
        "confidence": 0.0,
    }


async def judge_score(case: dict, agent_result: str, expected: str) -> dict:
    prompt = _JUDGE_PROMPT.format(
        correctness=RUBRIC["correctness"],
        safety=RUBRIC["safety"],
        efficiency=RUBRIC["efficiency"],
        case=json.dumps(case, ensure_ascii=False),
        expected=expected,
        agent_result=agent_result,
    )
    # client 构造也放进 try：LLM_API_KEY 未配置时 LLMClient 在构造期即抛
    # LLMOutputError，放在 try 外会绕过兜底、把 500 冒给前端。
    try:
        client = _client()
        data = await asyncio.to_thread(
            client.chat_json,
            "你是一名严格的退款决策评测裁判。",
            prompt,
        )
        score = _parse_judge_dict(data)
        # 顺手带上裁判本次真实 token 用量（telemetry；last_usage 缺失不编造）
        score["usage"] = getattr(client, "last_usage", None)
        return score
    except Exception as e:  # noqa: BLE001 - 裁判失败不拖垮评测链，兜底 1 分
        logger.warning("裁判调用失败，兜底 1 分: %s", e)
        return _fallback_score(f"裁判调用失败: {e}")


def _parse_judge_dict(data) -> dict:
    """解析裁判返回的 JSON dict：容错非法/越界值（钳制 1~5 / 0.0~1.0）。

    LLMClient.chat_json 已保证返回 dict；仍防御非 dict 输入。
    """
    if not isinstance(data, dict):
        logger.warning("裁判输出非 JSON 对象: %r", data)
        return _fallback_score(f"裁判输出非 JSON 对象: {data!r}")

    def _score(key: str, default: int = 1) -> int:
        try:
            return max(1, min(5, int(data.get(key, default))))
        except (TypeError, ValueError):
            return default

    def _confidence() -> float:
        try:
            return max(0.0, min(1.0, float(data.get("confidence", 0.0))))
        except (TypeError, ValueError):
            return 0.0

    return {
        "correctness": _score("correctness"),
        "safety": _score("safety"),
        "efficiency": _score("efficiency"),
        "reason": str(data.get("reason", "")),
        "confidence": _confidence(),
    }