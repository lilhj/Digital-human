"""AB 对比：不同 LLM 对风控+舆情评分的"合理性"与"稳定性"。

动机：deepseek-v3 对正常客诉（七天无理由退货）打出 fraud=0.8 明显误判，且同输入波动 0.3~0.7。
本文对比候选模型，量化两大指标：
- 合理性：正常场景应低分(LOW/MED)，恶意/高情绪应高分(HIGH)；每模型 ≤0.2 触发自动退款
- 稳定性：同一输入连打 3 次，max-min 波动幅度
供决策是否换 LLM_MODEL。

用法：cd backend && python ../scripts/ab_model_compare.py
"""
import json
import sys
import time

import httpx

from app.core.config import get_settings
from app.agents.llm import LLMClient

# 候选模型（来自 agicto 列表）
CANDIDATES = [
    "deepseek-v3",          # 基线：现在用的
    "deepseek-v4-pro",      # 新版 deepseek
    "gemini-2.5-pro",       # 评测裁判已在用
    "claude-sonnet-4-5-20250929",
    "gpt-5.1",
]

# 关键用例：(名称, description, evidence_text, 期望)
CASES = [
    ("七天无理由退货(正常)", "七天无理由退货，商品完好", "发票 退货运单", "低"),
    ("商品破损(常见客诉)", "商品破损，申请退款", "破损商品 退款 发票", "低~中"),
    ("明显恶意退款(高风险)", "多次退款申请，疑薅羊毛，拒绝发货", "重复发票", "高"),
    ("情绪激烈扬言投诉", "质量极差！要投诉你们！必须赔钱！", "商品照片 破损", "高"),
    ("空描述(无客诉)", "", "", "默认低"),
]

SYSTEM = (
    "你是电商风控与舆情双领域专家，一次输出两项评分。"
    "评分必须与等级对账：score>0.5对应HIGH，0.2<=score<=0.5对应MEDIUM，<0.2对应LOW。"
    "输出 JSON: {\"fraud_score\":0~1,\"fraud_features\":[字符串],"
    "\"sentiment_score\":0~1,\"risk_level\":\"LOW\"|\"MEDIUM\"|\"HIGH\",\"reason\":\"一句话\"}"
)

s = get_settings()


def score_once(model: str, desc: str, ev: str) -> dict:
    client = httpx.Client(timeout=60)
    try:
        r = client.post(
            f"{s.llm_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {s.llm_api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": f"客诉描述：{desc}\n凭证OCR文本：{ev}"},
                ],
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            },
        )
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"]
        data = json.loads(raw)
        return {
            "fraud": float(data.get("fraud_score", 0.5)),
            "senti": float(data.get("sentiment_score", 0.5)),
            "level": str(data.get("risk_level", "")).upper(),
        }
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    finally:
        client.close()


def main() -> None:
    print(f"模型候选: {CANDIDATES}")
    print(f"当前基线: {s.llm_model}")
    print("=" * 78)
    sum_rows = []
    for model in CANDIDATES:
        print(f"\n### 模型: {model}")
        print(f"{'用例':<18} {'fraud3次':<18} {'senti3次':<18} {'等级':<10} {'max-min(senti)'}")
        model_stats = {"合理": 0, "误判": 0}
        for name, desc, ev, expect in CASES:
            try:
                res = [score_once(model, desc, ev) for _ in range(3)]
                if any("error" in x for x in res):
                    err = next(x["error"] for x in res if "error" in x)
                    print(f"{name:<18} 调用失败: {err[:50]}")
                    continue
                frauds = [x["fraud"] for x in res]
                sentis = [x["senti"] for x in res]
                levels = [x["level"] for x in res]
                s_span = max(sentis) - min(sentis)
                # 合理性：期望"低"却给 HIGH(>0.5) 判定为误判
                verdict = "合理"
                if expect == "低" and (max(sentis) > 0.5 or max(frauds) > 0.5):
                    verdict = "⚠️误判(低风险给高分)"
                    model_stats["误判"] += 1
                elif expect == "高" and max(sentis) < 0.5:
                    verdict = "⚠️误判(高风险给低分)"
                    model_stats["误判"] += 1
                else:
                    model_stats["合理"] += 1
                print(
                    f"{name:<18} {'/'.join(f'{f:.2f}' for f in frauds):<18} "
                    f"{'/'.join(f'{s:.2f}' for s in sentis):<18} "
                    f"{'/'.join(levels):<10} {s_span:.2f}  {verdict}"
                )
            except Exception as e:  # noqa: BLE001
                print(f"{name:<18} 异常: {e}")
        sum_rows.append((model, model_stats["合理"], model_stats["误判"]))
        time.sleep(0.5)

    print("\n" + "=" * 78)
    print("汇总（合理性：低风险不当高分 + 高风险不当低分才算对）")
    print(f"{'模型':<22} {'合理':<6} {'误判':<6}")
    for model, ok, bad in sum_rows:
        print(f"{model:<22} {ok:<6} {bad:<6}")
    print("\n结论: 合理率高 且 误判少 且 波动小的模型更适合做风控/舆情打分。")


if __name__ == "__main__":
    main()