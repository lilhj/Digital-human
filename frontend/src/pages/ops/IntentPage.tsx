/** 意图监控大屏（工单8）：时间筛选 + 4 KPI（召回率红线≥90% / 幻觉率红线≤2%，达标直显）
 *  + 三意图分布环形图 + 识别后路由 + 识别明细（含识别方式：规则/LLM 双层）。
 *  数据源：GET /api/v1/intent/summary（后端运行期聚合 + 离线基准红线指标）。
 */
import { useEffect, useState } from "react";
import { card, td, th } from "../../theme";
import { getIntentSummary } from "../../api/client";
import type { IntentSummary } from "../../api/types";

const RANGES = [
  { key: "7d", label: "近 7 天" },
  { key: "30d", label: "近 30 天" },
  { key: "today", label: "今日" },
  { key: "all", label: "全部" },
];

/* ---------- 环形图（SVG stroke-dasharray 三段） ---------- */

function Donut({ data }: { data: { label: string; count: number; color: string }[] }) {
  const total = data.reduce((s, d) => s + d.count, 0) || 1;
  const R = 54;
  const C = 2 * Math.PI * R;
  let offset = 0;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
      <svg width="150" height="150" viewBox="0 0 150 150">
        <circle cx="75" cy="75" r={R} fill="none" stroke="#eee" strokeWidth="20" />
        {data.map((d) => {
          const frac = d.count / total;
          const dash = `${frac * C} ${C}`;
          const el = (
            <circle
              key={d.label}
              cx="75"
              cy="75"
              r={R}
              fill="none"
              stroke={d.color}
              strokeWidth="20"
              strokeDasharray={dash}
              strokeDashoffset={-offset * C}
              transform="rotate(-90 75 75)"
            />
          );
          offset += frac;
          return el;
        })}
        <text x="75" y="71" textAnchor="middle" fontSize="20" fontWeight="700" fill="#333">{total}</text>
        <text x="75" y="90" textAnchor="middle" fontSize="11" fill="#999">总识别</text>
      </svg>
      <div>
        {data.map((d) => (
          <div key={d.label} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, marginBottom: 8 }}>
            <span style={{ width: 10, height: 10, borderRadius: 2, background: d.color, display: "inline-block" }} />
            <span style={{ minWidth: 36 }}>{d.label}</span>
            <span style={{ color: "#777" }}>{d.count}（{((d.count / total) * 100).toFixed(1)}%）</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ---------- KPI 卡片（红线达标直显） ---------- */

function KpiCard({ label, value, redline, ok }: { label: string; value: string; redline: string; ok: boolean }) {
  return (
    <div style={{ ...card, padding: 14, textAlign: "center", borderTop: `3px solid ${ok ? "#28a745" : "#dc3545"}` }}>
      <div style={{ fontSize: 24, fontWeight: 700, color: ok ? "#1a7f37" : "#dc3545" }}>{value}</div>
      <div style={{ fontSize: 12, color: "#777", marginTop: 2 }}>{label}</div>
      <div style={{ fontSize: 11, marginTop: 6, color: ok ? "#1a7f37" : "#dc3545" }}>
        {ok ? "✓ 达标" : "✗ 未达标"}（红线 {redline}）
      </div>
    </div>
  );
}

export default function IntentPage() {
  const [range, setRange] = useState("7d");
  const [data, setData] = useState<IntentSummary | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    setError("");
    getIntentSummary(range)
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError((e as Error).message));
    return () => {
      alive = false;
    };
  }, [range]);

  const evalMetric = data?.eval;

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
        <h1 style={{ fontSize: 20, margin: 0 }}>🎯 意图监控</h1>
        <div style={{ display: "flex", gap: 8 }}>
          {RANGES.map((r) => (
            <button
              key={r.key}
              onClick={() => setRange(r.key)}
              style={{
                padding: "5px 14px",
                borderRadius: 14,
                border: range === r.key ? "2px solid #0d6efd" : "1px solid #ccc",
                background: range === r.key ? "#e7f1ff" : "#fff",
                cursor: "pointer",
                fontSize: 13,
              }}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>
      <p style={{ fontSize: 12, color: "#999", margin: "0 0 16px" }}>
        {error
          ? `加载失败：${error}`
          : data
            ? `真实运行期数据（${range}）· 双层意图识别：Node A 规则 → Node B LLM；红线指标来自离线基准`
            : "加载中..."}
      </p>

      {data && (
        <>
          {/* 4 KPI（验收红线：召回率 ≥90%、幻觉率 ≤2%） */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10, marginBottom: 8 }}>
            <div style={{ ...card, padding: 14, textAlign: "center", borderTop: "3px solid #0d6efd" }}>
              <div style={{ fontSize: 24, fontWeight: 700, color: "#0d6efd" }}>{data.total_identified.toLocaleString()}</div>
              <div style={{ fontSize: 12, color: "#777", marginTop: 2 }}>识别总数</div>
              <div style={{ fontSize: 11, marginTop: 6, color: "#999" }}>三意图合计</div>
            </div>
            <KpiCard
              label="意图召回率"
              value={`${(evalMetric!.recall * 100).toFixed(1)}%`}
              redline="≥90%"
              ok={evalMetric!.recall >= 0.9}
            />
            <KpiCard
              label="幻觉率"
              value={`${(evalMetric!.hallucination_rate * 100).toFixed(1)}%`}
              redline="≤2%"
              ok={evalMetric!.hallucination_rate <= 0.02}
            />
            <div style={{ ...card, padding: 14, textAlign: "center", borderTop: "3px solid #fd7e14" }}>
              <div style={{ fontSize: 24, fontWeight: 700, color: "#fd7e14" }}>{data.to_human}</div>
              <div style={{ fontSize: 12, color: "#777", marginTop: 2 }}>转人工数</div>
              <div style={{ fontSize: 11, marginTop: 6, color: "#999" }}>换货 + 兜底路由</div>
            </div>
          </div>
          <p style={{ fontSize: 11, color: "#999", margin: "0 0 16px" }}>
            离线基准：规则命中率 {(evalMetric!.rule_hit_rate * 100).toFixed(1)}% · Token 降幅{" "}
            {(evalMetric!.token_reduction * 100).toFixed(1)}%（红线 ≥40%）· 样本 {evalMetric!.samples} 条
          </p>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1.2fr", gap: 16, marginBottom: 16 }}>
            {/* 三意图分布环形图 */}
            <section style={card}>
              <h2 style={{ fontSize: 15, margin: "0 0 14px" }}>意图分布（退款 / 退货 / 换货）</h2>
              <Donut
                data={data.distribution.length > 0
                  ? data.distribution.map((d) => ({ label: d.label, count: d.count, color: d.color }))
                  : [{ label: "暂无", count: 0, color: "#eee" }]}
              />
            </section>

            {/* 识别后路由 */}
            <section style={card}>
              <h2 style={{ fontSize: 15, margin: "0 0 12px" }}>识别后路由</h2>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                <thead>
                  <tr style={{ background: "#f5f6fa" }}>
                    <th style={th}>路由</th>
                    <th style={th}>单量</th>
                    <th style={th}>说明</th>
                  </tr>
                </thead>
                <tbody>
                  {data.routes.map((r) => (
                    <tr key={r.route} style={{ borderBottom: "1px solid #eee" }}>
                      <td style={td}>{r.route}</td>
                      <td style={td}>{r.count}</td>
                      <td style={{ ...td, color: "#777", fontSize: 12 }}>{r.note}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          </div>

          {/* 识别明细（识别方式体现双层） */}
          <section style={card}>
            <h2 style={{ fontSize: 15, margin: "0 0 12px" }}>识别明细</h2>
            {data.details.length === 0 ? (
              <p style={{ fontSize: 13, color: "#888" }}>该时间范围内暂无意图识别记录</p>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                <thead>
                  <tr style={{ background: "#f5f6fa" }}>
                    <th style={th}>时间</th>
                    <th style={th}>用户输入</th>
                    <th style={th}>意图</th>
                    <th style={th}>识别方式</th>
                    <th style={th}>置信度</th>
                    <th style={th}>路由</th>
                  </tr>
                </thead>
                <tbody>
                  {data.details.map((d, i) => (
                    <tr key={i} style={{ borderBottom: "1px solid #eee" }}>
                      <td style={td}>{d.time}</td>
                      <td style={td}>{d.text}</td>
                      <td style={td}>{d.intent}</td>
                      <td style={td}>
                        <span
                          style={{
                            padding: "2px 8px",
                            borderRadius: 10,
                            fontSize: 12,
                            background: d.method === "规则" ? "#e6f4ea" : d.method === "LLM" ? "#e7f1ff" : "#fff3cd",
                            color: d.method === "规则" ? "#1a7f37" : d.method === "LLM" ? "#0d6efd" : "#856404",
                          }}
                        >
                          {d.method === "规则" ? "Node A 规则" : d.method === "LLM" ? "Node B LLM" : d.method}
                        </span>
                      </td>
                      <td style={{ ...td, color: d.conf < 0.7 ? "#dc3545" : undefined }}>{d.conf.toFixed(2)}</td>
                      <td style={{ ...td, color: d.route.includes("转人工") ? "#fd7e14" : undefined }}>{d.route}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      )}
    </div>
  );
}
