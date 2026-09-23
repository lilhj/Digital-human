"""视觉理解 Provider（凭证图片语义理解，Qwen2.5-VL via 本地 Ollama）。

从「OCR 抽文本」升级为「理解图像」：用视觉语言模型生成图片的结构化语义描述
（品类/损伤类型/严重度/描述/is_damaged/建议），供风控节点做「凭证与客诉描述
一致性校验」——不一致 → 风险分 +30 分。

设计（对齐现有 Provider 降级哲学）：
- Ollama 未启动 / 模型未就绪 / 推理超时 / 输出非标准 JSON → 返回
  VisionResult(status != "OK")，上层静默跳过一致性校验（不加分、不阻断），
  绝不因视觉链路异常阻塞主流程（与 PaddleOCR 的 Fake 降级同一原则）。
- 与 PaddleOCR 并存：OCR 抽文本留痕，VL 出语义理解，两者都进风控判定。
- 安全：VL 输出（含图片中可能携带的恶意文字）会再过一遍 Critic，命中即短路转人工。
"""
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

from app.core.config import get_settings

logger = logging.getLogger("vision")

# 参考提示词（用户提供 D:\d\参考提示词.py）：Qwen2.5-VL 对电子类商品凭证的破损分析
SYSTEM_PROMPT = """# Role
你是一名小米商城的专业售后审核专员。你的任务是分析用户上传的电子产品凭证图片，生成一段客观、详细且结构化的破损描述。

# Product Scope
主要涉及：智能手机（Xiaomi/Redmi）、平板、笔记本电脑、智能穿戴设备、电视、智能家居（摄像头/音箱）、配件（充电器/耳机）等。

# Workflow
1. **品类确认**：首先识别图片中的主体产品是什么（例如：手机屏幕、路由器外壳、耳机充电盒）。
2. **关键部位检查**：
   - **屏幕/显示面板**：是否有裂痕、漏液、坏点、划痕、脱胶。
   - **机身/外壳**：是否有磕碰、掉漆、变形、裂纹、碎裂。
   - **接口/按键**：充电口是否破损、按键是否脱落或卡死。
   - **包装/附件**：外包装盒是否严重挤压变形（影响二次销售），说明书/保修卡是否缺失或污损。
3. **文字提取 (OCR)**：尝试读取SN码、IMEI码或物流面单信息（如有）。

# Output Format
请严格按照以下 JSON 格式输出分析结果，不要包含多余的废话：

{
  "product_category": "识别到的产品品类（如：智能手机 / 智能家电 / 配件）",
  "damage_type": "损伤类型（例如：屏幕碎裂 / 外壳磕碰 / 包装挤压 / 接口损坏 / 无明显破损）",
  "severity": "严重程度（轻微 / 中等 / 严重 / 报废级）",
  "description": "一段简练的中文描述。必须包含具体的损伤位置（如屏幕左下角、Type-C接口处）、损伤形态（如放射状裂纹、深度划痕）以及受影响的部件。",
  "is_damaged": true/false,
  "suggestion": "基于图片的初步建议（例如：建议换屏 / 外观磨损不影响功能 / 需进一步检测）"
}

# Constraints
- 描述必须基于图片可见事实，严禁臆测内部故障。
- 如果图片模糊无法辨认细节，请在 description 中说明"图片清晰度不足，无法确认细节"。
- 语气保持客观中立，符合小米售后专业形象。"""


@dataclass
class VisionResult:
    product_category: str = ""
    damage_type: str = ""
    severity: str = ""
    description: str = ""
    is_damaged: bool | None = None
    suggestion: str = ""
    status: str = "OK"           # OK / UNAVAILABLE / PARSE_ERROR / TIMEOUT
    raw: str = ""
    elapsed_ms: int = 0
    usage: dict | None = None    # telemetry：VL 推理 token 用量（Ollama /api/chat 返回）

    @property
    def available(self) -> bool:
        """仅当语义描述可用（OK 且非空）时，才把视觉信息交给下游做一致性校验。"""
        return self.status == "OK" and bool(self.description)


class VisionProvider:
    """视觉理解接口（可替换为 Fake / Ollama 实现）。"""

    def analyze(self, image_path: str) -> VisionResult:
        raise NotImplementedError


class OllamaVisionProvider(VisionProvider):
    """Qwen2.5-VL via 本地 Ollama：调用 /api/chat 传 base64 图片，强 JSON 输出。"""

    def __init__(self, *, base_url: str | None = None, model: str | None = None,
                 timeout: int | None = None):
        s = get_settings()
        self.base_url = (base_url or s.ollama_base_url).rstrip("/")
        self.model = model or s.vision_model
        self.timeout = timeout or s.vision_timeout_seconds

    @property
    def available(self) -> bool:
        """探活：/api/tags 可达即认为模型服务就绪（3s 内，不做推理）。"""
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=3)
            return r.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    def analyze(self, image_path: str) -> VisionResult:
        t0 = time.time()
        try:
            image_b64 = base64.b64encode(Path(image_path).read_bytes()).decode("utf-8")
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": "请分析这张商品凭证图片中的破损情况。",
                     "images": [image_b64]},
                ],
                "stream": False,
                "format": "json",  # 强制模型输出 JSON
            }
            resp = requests.post(
                f"{self.base_url}/api/chat", json=payload, timeout=self.timeout
            )
            resp.raise_for_status()
            body = resp.json()
            content = body.get("message", {}).get("content", "")
            parsed = json.loads(content)
            return VisionResult(
                product_category=str(parsed.get("product_category", "")),
                damage_type=str(parsed.get("damage_type", "")),
                severity=str(parsed.get("severity", "")),
                description=str(parsed.get("description", "")),
                is_damaged=parsed.get("is_damaged"),
                suggestion=str(parsed.get("suggestion", "")),
                status="OK",
                raw=content,
                elapsed_ms=int((time.time() - t0) * 1000),
                usage=body.get("prompt_eval_count") is not None
                and {"prompt_tokens": body.get("prompt_eval_count", 0),
                     "completion_tokens": body.get("eval_count", 0)} or None,
            )
        except json.JSONDecodeError as e:
            logger.warning("VL 输出非标准 JSON（%s）: %s", image_path, e)
            return VisionResult(status="PARSE_ERROR", raw=str(e), elapsed_ms=int((time.time() - t0) * 1000))
        except requests.exceptions.Timeout:
            logger.warning("VL 推理超时（%s）: 静默降级跳过一致性校验", image_path)
            return VisionResult(status="TIMEOUT", elapsed_ms=int((time.time() - t0) * 1000))
        except Exception as e:  # noqa: BLE001 - 视觉链路异常绝不阻断主流程
            logger.warning("VL 不可用降级（%s）: %s", image_path, e)
            return VisionResult(status="UNAVAILABLE", elapsed_ms=int((time.time() - t0) * 1000))


class FakeVisionProvider(VisionProvider):
    """开发/测试默认：固定返回一份"屏幕碎裂"的语义描述（确定性，供演示与测试）。"""

    def analyze(self, image_path: str) -> VisionResult:
        return VisionResult(
            product_category="智能手机",
            damage_type="屏幕碎裂",
            severity="严重",
            description="图片显示手机屏幕有明显放射状裂纹，疑似摔落导致（Fake 视觉理解，演示用）",
            is_damaged=True,
            suggestion="建议换屏",
            status="OK",
        )


class NoopVisionProvider(VisionProvider):
    """隔离测试：不产生视觉信息，下游一致性校验直接跳过。"""

    def analyze(self, image_path: str) -> VisionResult:
        return VisionResult(status="UNAVAILABLE")
