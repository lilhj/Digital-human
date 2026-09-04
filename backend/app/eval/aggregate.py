"""评测结果聚合（三维：决策正确性 / 金额边界安全 / 理由质量）。"""


def aggregate_scores(results: list[dict]) -> dict:
    if not results:
        return {"correctness": 0.0, "safety": 0.0, "efficiency": 0.0, "total": 0.0, "count": 0}
    n = len(results)
    c = sum(r["correctness"] for r in results) / n
    s = sum(r["safety"] for r in results) / n
    e = sum(r["efficiency"] for r in results) / n
    return {
        "correctness": round(c, 2),
        "safety": round(s, 2),
        "efficiency": round(e, 2),
        "total": round((c + s + e) / 3, 2),
        "count": n,
    }
