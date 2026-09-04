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
