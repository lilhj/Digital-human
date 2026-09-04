"""LLM 客户端：调用 agicto.cn API（OpenAI 兼容），严格 JSON 输出。

防御性设计（裁决 D-004）：
- 超时 30s，重试 ≤2 次
- 解析失败抛 LLMOutputError，调用方（节点）负责 Fallback 转人工
"""
import json
import logging
import time
from dataclasses import dataclass

import httpx

from app.core.config import get_settings

logger = logging.getLogger("llm")


class LLMOutputError(Exception):
    """模型输出非法 JSON 或格式不符。"""


@dataclass
class LLMResult:
    raw: str
    data: dict


def _parse_usage(payload: dict | None) -> dict | None:
    """从 OpenAI 兼容响应的 usage 字段提取 token 计数。

    有的网关返回 {"prompt_tokens":n,"completion_tokens":n,"total_tokens":n}，
    个别网关缺字段/缺值（如流式或兼容层）。防御性提取：任一字段缺失即按 0 处理，
    完全无 usage 返回 None（诚实原则：没有就不编）。
    """
    if not isinstance(payload, dict):
        return None
    pt = payload.get("prompt_tokens")
    ct = payload.get("completion_tokens")
    if pt is None and ct is None:
        return None
    return {
        "prompt_tokens": int(pt or 0),
        "completion_tokens": int(ct or 0),
    }


class LLMClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None, timeout: int | None = None,
                 max_retries: int | None = None,
                 backoff_base: float | None = None, backoff_cap: float | None = None):
        s = get_settings()
        self.base_url = base_url or s.llm_base_url
        self.api_key = api_key or s.llm_api_key
        self.model = model or s.llm_model
        self.timeout = timeout or s.llm_timeout_seconds
        self.max_retries = max_retries if max_retries is not None else s.llm_max_retries
        # 指数退避（工单8 异常兜底）：第 attempt 次失败后休眠 base * 2**attempt，封顶 cap
        self.backoff_base = backoff_base if backoff_base is not None else 0.5
        self.backoff_cap = backoff_cap if backoff_cap is not None else 5.0
        # 最近一次成功调用的 token 用量（telemetry：供 Provider/节点落到 AgentRun）
        self.last_usage: dict | None = None

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def chat_json(self, system: str, user: str) -> dict:
        """请求模型输出严格 JSON，带指数退避重试；重试后仍失败抛 LLMOutputError。"""
        if not self.available:
            raise LLMOutputError("LLM 未配置 api_key（开发环境请用 FakeProvider）")

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system + "\n必须只输出 JSON，不要输出任何其他内容。"},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}

        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
                    resp.raise_for_status()
                    body = resp.json()
                    raw = body["choices"][0]["message"]["content"]
                    data = self._parse(raw)
                    # telemetry：记录本次真实 token 用量（供运营商页/详情页读取）
                    self.last_usage = _parse_usage(body.get("usage"))
                    return LLMResult(raw=raw, data=data).data
            except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError, LLMOutputError) as e:
                last_err = e
                logger.warning("LLM 调用失败(第%s次): %s", attempt + 1, e)
                # 指数退避：仅当还有重试机会时休眠（不阻塞最后一次失败的上抛）
                if attempt < self.max_retries:
                    sleep_s = min(self.backoff_cap, self.backoff_base * (2 ** attempt))
                    time.sleep(sleep_s)
        raise LLMOutputError(f"模型输出解析失败（重试 {self.max_retries} 次）: {last_err}")

    @staticmethod
    def _parse(raw: str) -> dict:
        raw = raw.strip()
        # 容忍 ```json 代码块包裹
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise LLMOutputError("输出不是 JSON 对象")
        return data
