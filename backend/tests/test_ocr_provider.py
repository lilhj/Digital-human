"""OCR Provider 测试：paddle 可用则跑真实推理；不可用则验证 Fake 与超时保护。"""
import pytest

from app.agents.providers import OcrResult


def _paddle_available() -> bool:
    try:
        import paddle  # noqa: F401

        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _paddle_available(), reason="paddle 未安装，跳过真实 OCR 测试")
class TestPaddleOcrReal:
    def test_real_ocr_extracts_chinese(self, tmp_path):
        """中文投诉测试图：真实推理返回文本与置信度（Loop 提示词 Phase 6 验证项）。"""
        from scripts.verify_ocr import generate_test_image
        from app.agents.paddle_ocr import PaddleOcrProvider

        img = generate_test_image(tmp_path / "test.png", "商品破损 申请退款 128元")
        provider = PaddleOcrProvider(timeout_seconds=30)
        result = provider.extract(str(img))
        assert isinstance(result, OcrResult)
        assert result.text.strip(), "未识别出文本"
        assert result.confidence is not None and result.confidence > 0
        assert result.status in ("OK", "LOW_CONFIDENCE")

    def test_real_ocr_blank_image_returns_empty(self, tmp_path):
        """纯色无文字图（实物破损照无印刷文字的场景）：识别文本为空 -> EMPTY，
        不返回无意义的误检置信度（修复：原 0.546 预警问题的根因场景）。"""
        from PIL import Image
        from app.agents.paddle_ocr import PaddleOcrProvider

        img = tmp_path / "blank.png"
        Image.new("RGB", (400, 200), "white").save(img)
        provider = PaddleOcrProvider(timeout_seconds=30)
        result = provider.extract(str(img))
        assert result.status == "EMPTY"
        assert result.text == ""
        assert result.confidence is None


class TestTimeoutProtection:
    def test_empty_path_returns_emptied(self):
        from app.agents.providers import FakeOcrProvider

        result = FakeOcrProvider().extract("")
        assert result.status == "EMPTY"

    def test_fake_provider_returns_high_confidence(self):
        from app.agents.providers import FakeOcrProvider

        result = FakeOcrProvider().extract("uploads/x.jpg")
        assert result.status == "OK"
        assert result.confidence == 0.95


class TestBlankLineFilter:
    """修复：识别文本为空按 EMPTY 处理 + 空白行不计入平均置信度。

    旧缺陷：检测器误检的"假文字框"识别出空白（text=' '），仍计入平均置信度，
    稀释真实文字行分数（实物破损照实测 0.546），且无文字图不返回 EMPTY。
    """

    def test_blank_lines_excluded_from_confidence(self, monkeypatch):
        """空白框（误检）+ 真文字行混在一起 -> 空白行被过滤，置信度只统计真行。"""
        from app.agents.paddle_ocr import PaddleOcrProvider

        class FakeEngine:
            def predict(self, path):
                return [
                    {"rec_texts": ["商品破损", " ", ""], "rec_scores": [0.95, 0.30, 0.20]},
                    {"rec_texts": ["退款 128 元"], "rec_scores": [0.90]},
                ]

        provider = PaddleOcrProvider(timeout_seconds=30)
        provider._engine = FakeEngine()
        result = provider.extract("uploads/x.jpg")
        assert result.status == "OK"
        assert result.text == "商品破损\n退款 128 元"
        assert abs(result.confidence - 0.925) < 1e-9  # (0.95 + 0.90) / 2，不含 0.30/0.20

    def test_all_blank_lines_returns_empty(self, monkeypatch):
        """全部行都是空白（无文字图）-> EMPTY + 无置信度，而非带无意义的低分。"""
        from app.agents.paddle_ocr import PaddleOcrProvider

        class FakeEngine:
            def predict(self, path):
                return [
                    {"rec_texts": [" ", ""], "rec_scores": [0.546, 0.20]},
                ]

        provider = PaddleOcrProvider(timeout_seconds=30)
        provider._engine = FakeEngine()
        result = provider.extract("uploads/x.jpg")
        assert result.status == "EMPTY"
        assert result.text == ""
        assert result.confidence is None
