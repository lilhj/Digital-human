#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工单8 周期性自动化测试入口（一键触发 + 基线回归监控 + 报告落盘）。

与 /api/v1/eval/periodic/run 共用 app.eval.periodic.run_periodic（同一份逻辑，
避免脚本与 API 行为漂移）。

用法：
    python -m scripts.run_periodic_eval            # 跑意图基准 + 决策 Golden（mock），写报告
    python -m scripts.run_periodic_eval --save-baseline   # 以当前结果为基线固化
    python -m scripts.run_periodic_eval --strict   # 红线/回归劣化直接非零退出（CI 严苛模式）

产物：
    docs/周期性自动化测试报告.md      （人读周期报告）
    docs/periodic_report.json         （结构化周期报告，前端数据源）
    docs/intent_benchmark_baseline.json  （基线，供回归对比；--save-baseline 刷新）
"""
import argparse
import sys
from pathlib import Path

# 允许以脚本方式运行（项目根在 backend/）
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.eval import periodic as pe  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="工单8 周期性自动化测试")
    ap.add_argument("--save-baseline", action="store_true", help="以当前结果为基线固化")
    ap.add_argument("--strict", action="store_true", help="红线/回归劣化直接非零退出")
    args = ap.parse_args()

    report = pe.run_periodic(save_baseline=args.save_baseline)

    print(f"[ok] 周期报告已写：{pe.periodic_md_file()}")
    print(f"[ok] 结构化产物已写：{pe.periodic_json_file()}")
    if args.save_baseline:
        print(f"[ok] 基线已固化：{pe.baseline_file()}")
    print(f"[summary] 样本={report['samples']} Recall={report['Recall'] * 100:.1f}% "
          f"幻觉={report['HallucinationRate'] * 100:.1f}% "
          f"Token降={report['TokenReduction'] * 100:.1f}%")

    red = report["red_lines"]
    regressions = [n for n in report["regression_notes"] if "劣化" in n]
    if args.strict and (not red["all_pass"] or regressions):
        print("[strict] 红线未达标或回归劣化，非零退出", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
