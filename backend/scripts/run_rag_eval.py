#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""RAG 客服评测一键跑批（与 /api/v1/eval/rag/run 共用 app.eval.rag_benchmark.run_rag_eval）。

用法：
    python -m scripts.run_rag_eval                # mock：确定性原文路径，跑 20 条用例
    python -m scripts.run_rag_eval --real         # real：走 LLM 完整链路（需 LLM_API_KEY）
    python -m scripts.run_rag_eval --strict       # 红线未达标非零退出（CI 严苛模式）

产物：
    docs/eval_report_rag.json          （结构化报告，前端评测中心数据源）
    docs/客服知识库_回归评测报告.md      （人读 Markdown）
"""
import argparse
import sys
from pathlib import Path

# 允许以脚本方式运行（项目根在 backend/）
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.eval import rag_benchmark  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="RAG 客服评测一键跑批")
    ap.add_argument("--real", action="store_true", help="走 LLM 完整链路（需 LLM_API_KEY）")
    ap.add_argument("--strict", action="store_true", help="红线未达标直接非零退出")
    args = ap.parse_args()

    report = rag_benchmark.run_rag_eval(use_llm=args.real)
    a = report["aggregate"]
    red = report["red_lines"]

    print(f"[ok] 结构化产物已写：{rag_benchmark.rag_json_file()}")
    print(f"[ok] Markdown 报告已写：{rag_benchmark.rag_md_file()}")
    print(f"[summary] 模式={report['mode']} "
          f"Recall={a['recall_n']}/{a['policy_total']} "
          f"答案={a['answer_n']}/{a['policy_total']} "
          f"拒答={a['refuse_n']}/{a['refuse_total']} "
          f"路由={a['route_n']}/{a['route_total']} "
          f"幻觉={a['halluc_n']}")
    for c in report["cases"]:
        if not c["pass"]:
            print(f"[fail] {c['id']} {c['scenario']} -> resp={c['resp_type']}")

    if args.strict and not red["all_pass"]:
        print("[strict] 红线未达标：", "；".join(red["fails"]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
