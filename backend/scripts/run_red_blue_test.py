#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工单6 任务三：红蓝对抗测试与拦截率压测（一键触发）。

与 /api/v1/security/red-blue/run 共用 app.security.red_blue.run_red_blue（同一份逻辑，
避免脚本与 API 行为漂移）。

用法（在 backend 目录下）：
    python scripts/run_red_blue_test.py            # 打印摘要 + 写 JSON/MD 双报告
    python scripts/run_red_blue_test.py --quiet   # 仅写报告文件

产物：
    docs/red_blue_test_report.json    （结构化报告，前端数据源）
    docs/red_blue_test_report.md      （人读 Markdown 报告）

攻击样本构成（覆盖规格要求：Base64 绕过 / 角色扮演越狱 / 多语言注入）：
- 中文注入、英文注入、中文越狱、英文越狱、Base64 绕过、跨语言（日/西/韩）。
- 含少量"刻意绕过规则库"的难样本（语义/变形），用于暴露规则引擎真实缺口。
"""
import argparse
import os
import sys

# scripts/ 上一级即 backend 根，确保能 import app
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
os.environ.setdefault("TESTING", "1")

from app.security import red_blue  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="仅写报告文件，不打印摘要")
    args = ap.parse_args()

    r = red_blue.run_red_blue()

    if not args.quiet:
        print(f"报告已写入：{red_blue.red_blue_md_file()}")
        print(f"结构化产物：{red_blue.red_blue_json_file()}")
        print(f"[指标] 拦截率={r['intercept_rate']:.1f}%  越狱={r['jb_rate']:.1f}%  "
              f"DLP漏报={r['dlp']['miss_rate']:.1f}%  误报={r['dlp']['fp_rate']:.1f}%")


if __name__ == "__main__":
    main()
