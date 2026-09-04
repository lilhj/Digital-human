"""演示：对 sample_evidence/ 里的凭证图跑真实 PaddleOCR。

用法: cd backend && python scripts/demo_ocr.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.paddle_ocr import PaddleOcrProvider

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = PROJECT_ROOT / "sample_evidence"
# 演示三类凭证
TARGETS = [
    "express_waybill.png",      # 快递单：运单号/寄收件人/地址 -> OCR 有大量文本
    "invoice_goods.png",        # 发票：商品/金额/税号 -> OCR 有文本
    "broken_screen_phone.png",  # 破损照片：屏幕裂纹 -> 基本无文字，LOW_CONFIDENCE
]


def main() -> None:
    ocr = PaddleOcrProvider(timeout_seconds=120.0)  # 预热期放宽超时

    # 第一次加载模型：下载/编译算子，耗时较长（静默日志会打）
    print("== 加载 PaddleOCR 模型（首次可能较慢）... ==")
    t0 = time.time()
    try:
        ocr.engine  # 触发懒加载
        warm_n = getattr(ocr, "warmup", None)
        if warm_n:
            warm_n()
    except Exception as e:  # noqa: BLE001
        print(f"模型加载失败: {e}")
        sys.exit(1)
    print(f"== 模型就绪（{time.time() - t0:.1f}s） ==")
    print()

    for name in TARGETS:
        p = SAMPLE_DIR / name
        if not p.exists():
            print(f"[跳过] 文件不存在: {p}")
            continue
        t = time.time()
        result = ocr.extract(str(p))
        elapsed = time.time() - t
        print("=" * 64)
        print(f"文件: {name}  ({elapsed:.1f}s)")
        print(f"状态: {result.status}   置信度: {result.confidence}")
        print("-" * 64)
        if result.text:
            for line in result.text.splitlines():
                print(f"  {line}")
        else:
            print("  （无文本：破损照片场景，OCR 提取不到有效文字）")
        print()


if __name__ == "__main__":
    main()