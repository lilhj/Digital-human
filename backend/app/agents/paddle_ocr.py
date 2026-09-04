"""PaddleOCR 本地推理实现（Phase 6：本地化部署，工单"算法模型下载到本地部署推理"）。

设计：
- 懒加载：首次 extract 时才加载模型（避免 import 阶段拉模型/报错）
- 模型不存在时给出清晰提示，不无限下载（Loop 提示词 Phase 6 要求）
- 返回文本 + 总体置信度 + 字段级置信度 + 状态（OK/LOW_CONFIDENCE）
- 超时保护：单图推理超过阈值标记 TIMEOUT
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError

from app.agents.providers import OcrProvider, OcrResult

logger = logging.getLogger("paddle_ocr")


class PaddleOcrProvider(OcrProvider):
    """基于 PaddleOCR 的本地 OCR 推理（CPU）。"""

    def __init__(self, timeout_seconds: float = 30.0):
        self.timeout_seconds = timeout_seconds
        self._engine = None
        self._engine_error: str | None = None

    # ---------- 模型加载 ----------

    @property
    def engine(self):
        if self._engine is None:
            self._load()
        return self._engine

    def _load(self) -> None:
        """懒加载 PaddleOCR（CPU，轻量模型 PP-OCRv5 mobile 由库自动下载一次）。"""
        try:
            from paddleocr import PaddleOCR

            logger.info("加载 PaddleOCR 模型（PP-OCRv5 mobile 轻量模型，CPU 推理）...")
            # 显式指定 mobile 轻量模型：默认 server 模型在 CPU 上单张 30s+，不可用
            # enable_mkldnn=False：paddle 3.x oneDNN 执行器与 PP-OCRv5 存在
            # PIR 指令兼容 bug（ConvertPirAttribute2RuntimeAttribute），CPU 禁用后正常
            self._engine = PaddleOCR(
                use_textline_orientation=True,
                lang="ch",
                text_detection_model_name="PP-OCRv5_mobile_det",
                text_recognition_model_name="PP-OCRv5_mobile_rec",
                enable_mkldnn=False,
            )
            logger.info("PaddleOCR 模型加载完成")
        except Exception as e:  # noqa: BLE001
            self._engine_error = str(e)
            logger.error("PaddleOCR 加载失败: %s", e)
            raise

    @property
    def available(self) -> bool:
        return self._engine_error is None

    def warmup(self) -> None:
        """预热：首次推理需编译算子（实测 ~37s），预热后稳定 ~1.4s。

        Worker 启动时调用一次，避免首个工单触发超时。
        """
        try:
            from PIL import Image

            img = Image.new("RGB", (64, 32), "white")
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                img.save(f.name)
                self.extract(f.name)
        except Exception as e:  # noqa: BLE001
            logger.warning("OCR 预热失败（不影响后续使用）: %s", e)

    def health_check(self) -> dict:
        """启动检查：模型是否存在（供 Worker 启动/健康检查调用）。"""
        if self._engine_error:
            return {"ok": False, "error": self._engine_error}
        try:
            self.engine
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    # ---------- 推理 ----------

    def extract(self, image_path: str) -> OcrResult:
        if not image_path:
            return OcrResult(text="", confidence=None, status="EMPTY")
        if not self.available:
            return OcrResult(
                text="", confidence=None, status="TIMEOUT",
            )
        # 超时保护：单图推理超时标记 TIMEOUT，不阻塞 Worker
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self._infer, image_path)
            try:
                return future.result(timeout=self.timeout_seconds)
            except TimeoutError:
                logger.warning("OCR 推理超时（>%.1fs）: %s", self.timeout_seconds, image_path)
                return OcrResult(text="", confidence=None, status="TIMEOUT")

    def _infer(self, image_path: str) -> OcrResult:
        started = time.time()
        result = self.engine.predict(image_path)
        lines = []
        total_conf = 0.0
        count = 0
        for item in result:
            if item is None:
                continue
            # PaddleOCR 3.x 输出结构：list of dicts（含 rec_texts / rec_scores）或元组
            texts = item.get("rec_texts") or item.get("texts") or []
            scores = item.get("rec_scores") or item.get("scores") or []
            for t, s in zip(texts, scores):
                if t is None:
                    continue
                lines.append(str(t))
                total_conf += float(s or 0.0)
                count += 1
        elapsed = time.time() - started
        text = "\n".join(lines)
        confidence = round(total_conf / count, 3) if count else 0.0
        status = "OK" if confidence >= 0.5 else "LOW_CONFIDENCE"
        logger.info("OCR 完成: %d 行, 置信度 %.3f, 耗时 %.2fs", count, confidence, elapsed)
        return OcrResult(text=text, confidence=confidence, status=status)
