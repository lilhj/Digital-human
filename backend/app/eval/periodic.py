"""工单8 周期性自动化测试：意图基准 A/B + 基线回归监控 + 红线判定 + 报告落盘。

被 scripts/run_periodic_eval.py 与 /api/v1/eval/periodic* 共用（脚本与一键触发同源，
避免两份逻辑漂移）。跑批后同时落：
- docs/periodic_report.json        结构化产物（前端周期测试区块的数据源）
- docs/周期性自动化测试报告.md      人读 Markdown 报告

验收红线（PRD §4.3 / 工单8）：
- 意图召回 Recall ≥ 0.90
- 幻觉率 ≤ 0.02
- 混合流 Token 降幅 ≥ 0.40
回归策略：相对基线任一红线劣化即记录告警（不强制失败，便于调度器持续采集）。
"""
import json
from datetime import datetime
from pathlib import Path

from app.eval import intent_benchmark as ib
from app.eval.report import base_dir

# 验收红线
RECALL_FLOOR = 0.90
HALLUC_FLOOR = 0.02
TOKEN_RED_FLOOR = 0.40


def baseline_file() -> Path:
    return base_dir() / "intent_benchmark_baseline.json"


def periodic_json_file() -> Path:
    """结构化周期报告（前端数据源）。"""
    return base_dir() / "periodic_report.json"


def periodic_md_file() -> Path:
    """人读周期报告 Markdown。"""
    return base_dir() / "周期性自动化测试报告.md"


def _load_baseline() -> dict | None:
    if baseline_file().exists():
        try:
            return json.loads(baseline_file().read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 基线损坏降级为无基线
            return None
    return None


def _regression_notes(cur: dict, base: dict | None) -> list[str]:
    if base is None:
        return ["无历史基线，本次结果作为新基线（可用「固化为基线」按钮保存）。"]
    notes: list[str] = []
    if cur["Recall"] < base.get("Recall", 1.0) - 1e-9:
        notes.append(f"召回劣化：{base['Recall'] * 100:.1f}% -> {cur['Recall'] * 100:.1f}%")
    if cur["HallucinationRate"] > base.get("HallucinationRate", 0.0) + 1e-9:
        notes.append(f"幻觉率上升：{base['HallucinationRate'] * 100:.1f}% -> {cur['HallucinationRate'] * 100:.1f}%")
    if cur["TokenReduction"] < base.get("TokenReduction", 1.0) - 1e-9:
        notes.append(f"Token 降幅劣化：{base['TokenReduction'] * 100:.1f}% -> {cur['TokenReduction'] * 100:.1f}%")
    if not notes:
        notes.append("相对基线无劣化，全部红线稳定。")
    return notes


def _red_line_status(cur: dict) -> dict:
    fails: list[str] = []
    if cur["Recall"] < RECALL_FLOOR:
        fails.append(f"召回 {cur['Recall'] * 100:.1f}% < {RECALL_FLOOR * 100:.0f}%")
    if cur["HallucinationRate"] > HALLUC_FLOOR:
        fails.append(f"幻觉率 {cur['HallucinationRate'] * 100:.1f}% > {HALLUC_FLOOR * 100:.0f}%")
    if cur["TokenReduction"] < TOKEN_RED_FLOOR:
        fails.append(f"Token 降幅 {cur['TokenReduction'] * 100:.1f}% < {TOKEN_RED_FLOOR * 100:.0f}%")
    return {
        "all_pass": not fails,
        "fails": fails,
        "recall_pass": cur["Recall"] >= RECALL_FLOOR,
        "hallucination_pass": cur["HallucinationRate"] <= HALLUC_FLOOR,
        "token_reduction_pass": cur["TokenReduction"] >= TOKEN_RED_FLOOR,
    }


def _build_md(cur: dict, regression: list[str], red: dict, decision_md: str, ts: str) -> str:
    md = ib.render_markdown(cur)
    md += "\n\n## 基线回归\n\n" + "\n".join(f"- {n}" for n in regression)
    if red["all_pass"]:
        md += "\n\n## 红线达标 ✅\n\n全部验收红线（Recall≥90% / 幻觉≤2% / Token降≥40%）通过。"
    else:
        md += "\n\n## 红线未达标 ❌\n\n" + "\n".join(f"- {f}" for f in red["fails"])
    md += decision_md
    md += f"\n\n---\n\n生成时间：{ts}"
    return md


def run_periodic(save_baseline: bool = False) -> dict:
    """跑一次周期测试：意图基准 + 决策 Golden（mock，尽力而为）-> 基线回归 -> 红线判定 -> 落盘 JSON+MD。

    返回结构化结果（与落盘的 periodic_report.json 一致），供 API/脚本直接返回前端。
    """
    base_dir().mkdir(parents=True, exist_ok=True)
    ts = datetime.now().isoformat(timespec="seconds")

    # 1) 意图识别基准（A/B）
    cur = ib.run_intent_benchmark()

    # 2) 决策 Golden 评测（mock，离线可跑，回归决策策略）——尽力而为，失败不阻断周期报告
    decision_md = ""
    try:
        from app.eval import harness

        harness.main(real=False)
        decision_md = "\n\n## 决策 Golden 评测（mock）\n\n详见 `docs/评测报告.md`（已刷新）。"
    except Exception:  # noqa: BLE001 - 决策评测失败仅跳过该段，周期报告仍可产出
        decision_md = "\n\n> 决策 Golden 评测本次跳过（未产出 `docs/评测报告.md`）。"

    # 3) 基线对比 + 红线
    base = None if save_baseline else _load_baseline()
    regression = _regression_notes(cur, base)
    red = _red_line_status(cur)

    # 4) 落盘（JSON + Markdown 双产物）
    report = {
        "ok": True,
        "generated_at": ts,
        "samples": cur["samples"],
        "rule_hit_rate": cur["rule_hit_rate"],
        "Recall": cur["Recall"],
        "HallucinationRate": cur["HallucinationRate"],
        "TokenReduction": cur["TokenReduction"],
        "TTFTReduction": cur["TTFTReduction"],
        # 本次刚固化基线也算"已有基线"（回归对比自下一次起生效）
        "has_baseline": (base is not None) or save_baseline,
        "regression_notes": regression,
        "red_lines": red,
    }
    periodic_json_file().write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    periodic_md_file().write_text(
        _build_md(cur, regression, red, decision_md, ts), encoding="utf-8"
    )

    # 5) 固化基线
    if save_baseline:
        baseline_file().write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")

    return report


def load_periodic() -> dict:
    """读取最近一次周期测试产物；未跑过 -> ok:false（前端据此引导先一键触发）。"""
    path = periodic_json_file()
    if not path.exists():
        return {
            "ok": False,
            "error": "尚无周期测试报告，请点击「一键触发」运行",
            "has_report": False,
            "generated_at": None,
        }
    data = json.loads(path.read_text(encoding="utf-8"))
    data["has_report"] = True
    data["ok"] = True
    return data
