"""Phase 6 OCR 本地化验证脚本（Loop 验证：中文投诉测试图 -> 文本+置信度+低置信路由）。

用法: cd backend && python -m scripts.verify_ocr
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw, ImageFont

from app.agents.factory import create_ocr_provider

OUT_DIR = Path(__file__).resolve().parents[2] / "uploads"


def _find_chinese_font() -> str | None:
    candidates = [
        r"C:\Windows\Fonts\simhei.ttf",  # 黑体
        r"C:\Windows\Fonts\msyh.ttc",  # 微软雅黑
        r"C:\Windows\Fonts\simsun.ttc",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    return None


def generate_test_image(path: Path, text: str = "商品破损 申请退款 128元") -> Path:
    """用中文字体生成白底黑字投诉测试图。"""
    font_path = _find_chinese_font()
    if font_path is None:
        raise RuntimeError("未找到中文字体（simhei/msyh/simsun）")
    font = ImageFont.truetype(font_path, 36)
    img = Image.new("RGB", (640, 200), "white")
    draw = ImageDraw.Draw(img)
    draw.text((20, 60), text, fill="black", font=font)
    img.save(path)
    return path


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    img_path = generate_test_image(OUT_DIR / "ocr_test.png")

    provider = create_ocr_provider()
    print(f"测试图片: {img_path}")
    result = provider.extract(str(img_path))

    print(f"OCR 文本: {result.text!r}")
    print(f"OCR 置信度: {result.confidence}")
    print(f"解析状态: {result.status}")
    assert result.text.strip(), "OCR 未识别出文本"
    assert result.confidence is not None and result.confidence > 0, "OCR 置信度异常"
    print("✅ 本地 OCR 推理验证通过")


if __name__ == "__main__":
    main()
