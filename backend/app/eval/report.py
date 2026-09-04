"""评测报告产物路径：mock/real 双模式分文件 + EVAL_REPORT_DIR 环境变量覆盖（测试隔离）。

默认落在项目根 docs/ 下：
- mock -> docs/eval_report.json        + docs/评测报告.md
- real -> docs/eval_report_real.json   + docs/评测报告_real.md
"""
import os
from pathlib import Path

_DEFAULT_DIR = Path(__file__).resolve().parents[3] / "docs"


def base_dir() -> Path:
    return Path(os.environ["EVAL_REPORT_DIR"]) if os.environ.get("EVAL_REPORT_DIR") else _DEFAULT_DIR


def report_file(real: bool = False) -> Path:
    """评测 JSON 产物路径（前端评测页的数据源）。"""
    return base_dir() / ("eval_report_real.json" if real else "eval_report.json")


def md_file(real: bool = False) -> Path:
    """评测 Markdown 产物路径（给人可读的报告）。"""
    return base_dir() / ("评测报告_real.md" if real else "评测报告.md")