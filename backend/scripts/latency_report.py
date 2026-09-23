"""延迟统计报告（面试/复盘用）：一键跑出全库真实耗时数据。

用法: cd backend && python -m scripts.latency_report [--limit 200]

数据源：agent_runs 表（每个节点落一条轨迹，含 duration_ms）。
输出三块：
1. 各节点耗时（次数/均值/P50/P95/最大）—— 定位瓶颈在哪个节点
2. 端到端单案件总耗时（按 case 汇总各节点）—— 用户实际等待
3. 案件状态分布 + 吞吐估算 —— 并发能力现状

只读，不写库。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.core.database import SessionLocal

# 节点耗时（按均值降序，让瓶颈一眼可见）。
# --limit N：仅统计最近 N 个案件，避免全库历史数据稀释近期表现。
_NODE_SQL = """
SELECT agent_name,
       count(*)                                   AS n,
       round(avg(duration_ms))                    AS avg_ms,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms)) AS p50,
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms)) AS p95,
       max(duration_ms)                           AS max_ms
FROM agent_runs
WHERE duration_ms IS NOT NULL
  AND (:limit = 0 OR case_id IN (
        SELECT id FROM refund_cases ORDER BY id DESC LIMIT :limit))
GROUP BY agent_name
ORDER BY avg_ms DESC NULLS LAST
"""

# 端到端：按 case 汇总节点耗时（近似用户等待，不含排队时间）
_E2E_SQL = """
SELECT count(*)                                    AS cases,
       round(avg(t))                               AS avg_ms,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY t)) AS p50,
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY t)) AS p95,
       max(t)                                      AS max_ms
FROM (SELECT case_id, sum(duration_ms) AS t FROM agent_runs
      WHERE (:limit = 0 OR case_id IN (
            SELECT id FROM refund_cases ORDER BY id DESC LIMIT :limit))
      GROUP BY case_id) s
"""

_STATUS_SQL = "SELECT status, count(*) AS n FROM refund_cases GROUP BY status ORDER BY n DESC"


def _fmt_ms(v) -> str:
    """毫秒友好显示：>=1000ms 转秒。"""
    if v is None:
        return "-"
    v = float(v)
    return f"{v / 1000:.1f}s" if v >= 1000 else f"{v:.0f}ms"


def main() -> None:
    ap = argparse.ArgumentParser(description="延迟统计报告")
    ap.add_argument("--limit", type=int, default=0, help="仅统计最近 N 个案件（0=全库）")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        print("=" * 72)
        print("延迟统计报告（数据源：agent_runs.duration_ms）")
        scope = f"最近 {args.limit} 个案件" if args.limit else "全库"
        print(f"统计范围：{scope}")
        print("=" * 72)

        # ---- 1. 各节点耗时 ----
        print("\n【各节点耗时】按均值降序，瓶颈一眼可见")
        print(f"  {'节点':<14}{'次数':>7}{'均值':>10}{'P50':>10}{'P95':>10}{'最大':>10}")
        print("  " + "-" * 68)
        rows = db.execute(text(_NODE_SQL), {"limit": args.limit}).fetchall()
        if not rows:
            print("  （无数据：agent_runs 表为空，先跑几个工单）")
        for r in rows:
            print(
                f"  {r[0]:<14}{r[1]:>7}{_fmt_ms(r[2]):>10}"
                f"{_fmt_ms(r[3]):>10}{_fmt_ms(r[4]):>10}{_fmt_ms(r[5]):>10}"
            )

        # ---- 2. 端到端总耗时 ----
        print("\n【端到端单案件总耗时】各节点求和（近似用户等待，不含队列排队）")
        r = db.execute(text(_E2E_SQL), {"limit": args.limit}).fetchone()
        if r and r[0]:
            print(f"  案件数：{r[0]}")
            print(f"  平均：{_fmt_ms(r[1])}    P50：{_fmt_ms(r[2])}    P95：{_fmt_ms(r[3])}    最慢：{_fmt_ms(r[4])}")
        else:
            print("  （无数据）")

        # ---- 3. 状态分布 + 吞吐估算 ----
        print("\n【案件状态分布】")
        for r in db.execute(text(_STATUS_SQL)).fetchall():
            print(f"  {r[0]:<18}{r[1]:>6}")

        print("\n【吞吐估算】Worker 单进程串行消费")
        e2e = db.execute(text(_E2E_SQL), {"limit": args.limit}).fetchone()
        if e2e and e2e[1]:
            avg_s = float(e2e[1]) / 1000
            print(f"  单 Worker 串行：约 {60 / avg_s:.1f} 单/分钟（基于平均 {avg_s:.1f}s）")
            print(f"  多开 N 个 Worker（消费名唯一，架构已支持）→ 近似线性 N 倍")
            print("  优化点：FRAUD 的 3 次 LLM 采样改并行可再砍 ~60% 端到端耗时")
        print()
    finally:
        db.close()


if __name__ == "__main__":
    main()
