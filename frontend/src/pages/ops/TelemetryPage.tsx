/** 系统监控（对标信息架构 §2 域6 + §4）：Langfuse 上报管道 + 节点真实时延 + DLQ 死信 + 优化红线。
 *  数据源：GET /api/v1/telemetry/summary。
 *  诚实原则：Token/成本未采集（LLM 为 Fake/规则引擎），不展示假数字，接入真实 LLM 计费后再补。
 */
import { useCallback, useEffect, useState } from "react";
import { getLangfuseTraces, getTelemetrySummary } from "../../api/client";
import type { LangfuseTrace, LangfuseTracesResponse, TelemetrySummary } from "../../api/types";
import { card, td, th } from "../../theme";

export default function TelemetryPage() {
  const [data, setData] = useState<TelemetrySummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [lfData, setLfData] = useState<LangfuseTracesResponse | null>(null);
  const [lfLoading, setLfLoading] = useState(true);
  const [expandedTrace, setExpandedTrace] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setData(await getTelemetrySummary());
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadLf = useCallback(async () => {
    setLfLoading(true);
    try {
      setLfData(await getLangfuseTraces(10));
    } catch (e) {
      setLfData({ ok: false, error: e instanceof Error ? e.message : "加载失败", traces: [] });
    } finally {
      setLfLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    void loadLf();
  }, [load, loadLf]);

  const lf = data?.langfuse;
  const lfIssues = [
    !lf?.module_installed && "langfuse SDK 未安装（pip install langfuse）",
    lf?.module_installed && !lf?.keys_configured && "LANGFUSE_SECRET_KEY / PUBLIC_KEY 未配置",
    !lf?.enabled && "LANGFUSE_ENABLED=false（上报已关闭）",
  ].filter(Boolean) as string[];

  const maxAvg = Math.max(1, ...(data?.node_latency ?? []).map((n) => n.avg_ms));

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
        <h1 style={{ fontSize: 20, margin: 0 }}>📈 系统监控</h1>
        <button
          onClick={() => {
            void load();
            void loadLf();
          }}
          style={{ padding: "6px 16px", background: "#0d6efd", color: "#fff", border: "none", borderRadius: 6, cursor: "pointer", fontSize: 13 }}
        >
          ↻ 刷新
        </button>
      </div>
      <p style={{ fontSize: 12, color: "#999", margin: "0 0 16px" }}>
        数据源：AgentRun 时延聚合 + spool SQLite 上报队列 + Redis DLQ 死信 · 单 case 链路 trace 见详情页时间线（内嵌）
      </p>

      {error && (
        <div style={{ ...card, padding: 20, marginBottom: 16, textAlign: "center", color: "#8a6d00", background: "#fffbe6" }}>
          {error}
        </div>
      )}
      {loading && <div style={{ ...card, padding: 24, textAlign: "center", color: "#999" }}>加载中...</div>}

      {!loading && data && (
        <>
          {/* KPI */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10, marginBottom: 16 }}>
            {[
              { label: "端到端平均时延", value: `${(data.end_to_end.avg_ms / 1000).toFixed(2)}s`, sub: `P95 ${(data.end_to_end.p95_ms / 1000).toFixed(2)}s · ${data.end_to_end.count} 案`, color: "#0d6efd" },
              { label: "Trace 队列 pending", value: `${data.spool.pending}`, sub: `累计入队 ${data.spool.total}`, color: data.spool.pending > 0 ? "#fd7e14" : "#1a7f37" },
              { label: "Trace 上报失败", value: `${data.spool.failed}`, sub: "重试 5 次后落 failed", color: data.spool.failed > 0 ? "#dc3545" : "#1a7f37" },
              { label: "DLQ 死信", value: `${data.dlq.length}`, sub: "意图识别异常兜底队列", color: data.dlq.length > 0 ? "#6f42c1" : "#1a7f37" },
            ].map((k) => (
              <div key={k.label} style={{ ...card, padding: 14, textAlign: "center" }}>
                <div style={{ fontSize: 24, fontWeight: 700, color: k.color }}>{k.value}</div>
                <div style={{ fontSize: 12, color: "#777" }}>{k.label}</div>
                <div style={{ fontSize: 11, color: "#aaa", marginTop: 2 }}>{k.sub}</div>
              </div>
            ))}
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1.2fr", gap: 16, marginBottom: 16 }}>
            {/* Langfuse 上报管道 */}
            <section style={card}>
              <h2 style={{ fontSize: 15, margin: "0 0 12px" }}>Langfuse 上报管道</h2>
              <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 12 }}>
                {[
                  { k: "上报开关 LANGFUSE_ENABLED", ok: !!lf?.enabled, v: lf?.enabled ? "开启" : "关闭" },
                  { k: "SDK 安装", ok: !!lf?.module_installed, v: lf?.module_installed ? "已安装" : "未安装" },
                  { k: "SECRET/PUBLIC_KEY", ok: !!lf?.keys_configured, v: lf?.keys_configured ? "已配置" : "未配置" },
                ].map((r) => (
                  <div key={r.k} style={{ display: "flex", justifyContent: "space-between", fontSize: 13, padding: "6px 10px", background: "#f8f9fc", borderRadius: 6 }}>
                    <span style={{ color: "#555" }}>{r.k}</span>
                    <span style={{ fontWeight: 600, color: r.ok ? "#1a7f37" : "#dc3545" }}>{r.v}</span>
                  </div>
                ))}
                <div style={{ fontSize: 12, color: "#999" }}>上报目标：{lf?.base_url}</div>
              </div>
              {lfIssues.length > 0 ? (
                <div style={{ fontSize: 12, color: "#8a6d00", background: "#fffbe6", borderRadius: 6, padding: 10 }}>
                  当前为「spool 落盘降级」模式，云端不会有数据。要打通：
                  <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                    {lfIssues.map((i) => (
                      <li key={i}>{i}</li>
                    ))}
                  </ul>
                  <div style={{ marginTop: 6 }}>配置后重启后端，pending 队列会自动退避重试补报。</div>
                </div>
              ) : (
                <div style={{ fontSize: 12, color: "#1a7f37" }}>管道配置完整，上报正常。</div>
              )}
            </section>

            {/* spool 最近条目 */}
            <section style={card}>
              <h2 style={{ fontSize: 15, margin: "0 0 12px" }}>上报队列最近条目（spool）</h2>
              {data.spool.recent.length === 0 ? (
                <p style={{ fontSize: 13, color: "#999", textAlign: "center", padding: 16 }}>队列为空 —— 尚无节点 Trace 入队</p>
              ) : (
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                  <thead>
                    <tr style={{ background: "#f5f6fa" }}>
                      <th style={th}>时间</th>
                      <th style={th}>节点</th>
                      <th style={th}>case</th>
                      <th style={th}>耗时</th>
                      <th style={th}>状态</th>
                      <th style={th}>重试</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.spool.recent.map((r, i) => (
                      <tr key={i} style={{ borderBottom: "1px solid #eee" }}>
                        <td style={{ ...td, color: "#777", fontSize: 11 }}>{r.time}</td>
                        <td style={td}>{r.node}</td>
                        <td style={td}>#{r.case_id}</td>
                        <td style={td}>{r.duration_ms}ms</td>
                        <td style={{ ...td, fontWeight: 600, color: r.status === "pending" ? "#fd7e14" : "#dc3545" }}>{r.status}</td>
                        <td style={td}>{r.attempts}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1.2fr 1fr", gap: 16 }}>
            {/* 节点真实时延 */}
            <section style={card}>
              <h2 style={{ fontSize: 15, margin: "0 0 12px" }}>节点平均时延（AgentRun 实测）</h2>
              {data.node_latency.map((n) => (
                <div key={n.node} style={{ marginBottom: 12 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13, marginBottom: 4 }}>
                    <span>
                      {n.label}
                      <span style={{ fontSize: 11, color: "#999", marginLeft: 6 }}>{n.count} 次</span>
                    </span>
                    <span style={{ fontWeight: 600 }}>
                      {n.avg_ms}ms
                      <span style={{ fontSize: 11, color: "#999", marginLeft: 6 }}>P95 {n.p95_ms}ms</span>
                    </span>
                  </div>
                  <div style={{ height: 8, background: "#eee", borderRadius: 4, overflow: "hidden" }}>
                    <div
                      style={{
                        width: `${(n.avg_ms / maxAvg) * 100}%`,
                        height: "100%",
                        background: n.avg_ms > 1000 ? "#fd7e14" : "#0d6efd",
                      }}
                    />
                  </div>
                </div>
              ))}
            </section>

            {/* DLQ + 优化红线 */}
            <section style={card}>
              <h2 style={{ fontSize: 15, margin: "0 0 12px" }}>DLQ 死信队列（最近 {data.dlq.items.length} 条 / 共 {data.dlq.length}）</h2>
              {data.dlq.items.length === 0 ? (
                <p style={{ fontSize: 12, color: "#1a7f37", padding: "8px 0" }}>✓ 队列为空 —— 意图识别等阶段无未处理异常</p>
              ) : (
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                  <thead>
                    <tr style={{ background: "#f5f6fa" }}>
                      <th style={th}>时间</th>
                      <th style={th}>阶段</th>
                      <th style={th}>case</th>
                      <th style={th}>错误</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.dlq.items.map((d, i) => (
                      <tr key={i} style={{ borderBottom: "1px solid #eee" }}>
                        <td style={{ ...td, color: "#777", fontSize: 11 }}>{d.time}</td>
                        <td style={td}>{d.stage}</td>
                        <td style={td}>#{d.case_id}</td>
                        <td style={{ ...td, color: "#b02a37", fontSize: 11 }}>{d.error}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <h2 style={{ fontSize: 15, margin: "16px 0 8px" }}>工单8 优化红线（离线基准实测）</h2>
              <div style={{ fontSize: 13, display: "flex", flexDirection: "column", gap: 6 }}>
                <div style={{ display: "flex", justifyContent: "space-between", padding: "6px 10px", background: "#f6fcf8", border: "1px solid #e6f4ea", borderRadius: 6 }}>
                  <span>Prompt Token 压缩（目标 ≥40%）</span>
                  <span style={{ fontWeight: 600, color: data.optimization.token_reduction >= 0.4 ? "#1a7f37" : "#dc3545" }}>
                    ↓ {(data.optimization.token_reduction * 100).toFixed(1)}%
                  </span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", padding: "6px 10px", background: "#f6fcf8", border: "1px solid #e6f4ea", borderRadius: 6 }}>
                  <span>规则层命中率（高置信零 LLM）</span>
                  <span style={{ fontWeight: 600, color: "#1a7f37" }}>{(data.optimization.rule_hit_rate * 100).toFixed(1)}%</span>
                </div>
                <div style={{ fontSize: 11, color: "#999" }}>Token/成本明细待接入真实 LLM 计费后补充（当前风险/意图 LLM 为降级实现）</div>
              </div>
            </section>
          </div>

          {/* Langfuse 云端真实数据（public API 直读） */}
          <section style={{ ...card, marginTop: 16 }}>
            <h2 style={{ fontSize: 15, margin: "0 0 4px" }}>Langfuse 云端真实数据（public API 直读）</h2>
            <p style={{ fontSize: 12, color: "#999", margin: "0 0 12px" }}>
              直接拉取 langfuse.com 云端已上报的 Trace，验证「spool 落盘 → flush 上云」链路真实入云；
              只展示 Trace 元数据与观测名，不回传 input/output 内容。
            </p>
            {lfLoading ? (
              <div style={{ padding: 16, textAlign: "center", color: "#999" }}>读取云端中...</div>
            ) : !lfData || !lfData.ok ? (
              <div style={{ fontSize: 13, color: "#8a6d00", background: "#fffbe6", borderRadius: 6, padding: 12 }}>
                云端读取不可用：{lfData?.error ?? "未知错误"}。检查 LANGFUSE_SECRET_KEY / PUBLIC_KEY 是否配置、网络是否可达。
              </div>
            ) : (
              <>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10, marginBottom: 12 }}>
                  {[
                    { label: "云端 Trace 总数", value: String(lfData.total_traces ?? 0), color: "#1a7f37" },
                    { label: "云端 Observation 总数", value: String(lfData.total_observations ?? 0), color: "#0d6efd" },
                    { label: "本页展示（最近）", value: String(lfData.traces.length), color: "#6f42c1" },
                  ].map((k) => (
                    <div key={k.label} style={{ padding: "10px", textAlign: "center", background: "#f8f9fc", borderRadius: 6 }}>
                      <div style={{ fontSize: 20, fontWeight: 700, color: k.color }}>{k.value}</div>
                      <div style={{ fontSize: 11, color: "#777" }}>{k.label}</div>
                    </div>
                  ))}
                </div>
                {lfData.traces.length === 0 ? (
                  <p style={{ fontSize: 13, color: "#999", textAlign: "center", padding: 16 }}>云端暂无 Trace —— 走一笔新案件（节点上报）后刷新</p>
                ) : (
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                    <thead>
                      <tr style={{ background: "#f5f6fa" }}>
                        <th style={th}>时间</th>
                        <th style={th}>Trace ID</th>
                        <th style={th}>名称</th>
                        <th style={th}>节点</th>
                        <th style={th}>case</th>
                        <th style={th}>耗时</th>
                        <th style={th}>观测数</th>
                      </tr>
                    </thead>
                    <tbody>
                      {lfData.traces.map((t) => (
                        <LfTraceRow
                          key={t.id}
                          t={t}
                          expanded={expandedTrace === t.id}
                          onToggle={() => setExpandedTrace(expandedTrace === t.id ? null : t.id)}
                        />
                      ))}
                    </tbody>
                  </table>
                )}
              </>
            )}
          </section>
        </>
      )}
    </div>
  );
}

function LfTraceRow({
  t,
  expanded,
  onToggle,
}: {
  t: LangfuseTrace;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <>
      <tr
        onClick={onToggle}
        style={{ borderBottom: "1px solid #eee", cursor: "pointer", background: expanded ? "#f0f6ff" : undefined }}
      >
        <td style={{ ...td, color: "#777", fontSize: 11, whiteSpace: "nowrap" }}>
          {t.timestamp ? new Date(t.timestamp).toLocaleString("zh-CN", { hour12: false }) : "-"}
        </td>
        <td style={{ ...td, fontFamily: "monospace", fontSize: 11 }}>{t.id.slice(0, 8)}…</td>
        <td style={td}>{t.name || "-"}</td>
        <td style={td}>{t.node || "-"}</td>
        <td style={td}>{t.case_id != null ? `#${t.case_id}` : "-"}</td>
        <td style={td}>
          <span style={{ color: t.duration_ms != null && t.duration_ms > 1000 ? "#fd7e14" : "#333" }}>
            {t.duration_ms != null ? `${(t.duration_ms / 1000).toFixed(1)}s` : "-"}
          </span>
        </td>
        <td style={{ ...td, fontWeight: 600, color: "#0d6efd" }}>{t.obs_count}</td>
      </tr>
      {expanded && (
        <tr style={{ background: "#fafbff" }}>
          <td colSpan={7} style={{ padding: "4px 10px 12px" }}>
            {t.observations.length === 0 ? (
              <div style={{ fontSize: 12, color: "#999", padding: "4px 0" }}>该 Trace 无观测（仅元数据）</div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {t.observations.map((o) => (
                  <div
                    key={o.id}
                    style={{ display: "flex", justifyContent: "space-between", fontSize: 12, padding: "4px 8px", background: "#f2f3f7", borderRadius: 4 }}
                  >
                    <span>
                      <span
                        style={{
                          display: "inline-block",
                          width: 8,
                          height: 8,
                          borderRadius: 4,
                          marginRight: 6,
                          background:
                            o.type === "GENERATION" ? "#0d6efd" : o.type === "AGENT" ? "#6f42c1" : o.type === "EVENT" ? "#fd7e14" : "#20c997",
                          verticalAlign: "middle",
                        }}
                      />
                      {o.type} · {o.name || "（无名称）"}
                    </span>
                    <span style={{ color: "#777" }}>{o.latency_ms != null ? `${o.latency_ms}ms` : "-"}</span>
                  </div>
                ))}
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}
