/** 评测中心（对标需求文档 §2.2 / §4.3）：Golden Dataset + LLM-as-judge 三维评分。
 *  数据源：GET /api/v1/eval/report（最近一次报告）+ POST /api/v1/eval/run（MANAGER 一键跑批）。
 *  未运行过评测时后端返回 404，页面引导先跑批。
 */
import { useCallback, useEffect, useState } from "react";
import { getEvalReport, getPeriodicReport, getRagReport, getRole, runEval, runPeriodicReport, runRagReport } from "../../api/client";
import type { EvalReport, PeriodicReport, RagReport } from "../../api/types";
import { card, td, th } from "../../theme";

const KIND_LABEL: Record<string, string> = {
  decision: "决策边界",
  security: "金额边界安全",
  quality: "理由质量",
};

const MODE_LABEL: Record<string, string> = {
  mock: "离线确定性打分",
  real: "LLM-as-a-judge 真实裁判",
};

export default function EvalPage() {
  const [real, setReal] = useState(false); // false=离线模式 / true=真实裁判
  const [report, setReport] = useState<EvalReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  // 工单8 周期测试（持续自测区块）
  const [periodic, setPeriodic] = useState<PeriodicReport | null>(null);
  const [periodicLoading, setPeriodicLoading] = useState(true);
  const [periodicRunning, setPeriodicRunning] = useState(false);
  // 买家侧 RAG 客服评测（5 维指标 + 红线）
  const [rag, setRag] = useState<RagReport | null>(null);
  const [ragLoading, setRagLoading] = useState(true);
  const [ragRunning, setRagRunning] = useState(false);
  const isManager = getRole() === "MANAGER";

  const load = useCallback(async (mode: boolean) => {
    setLoading(true);
    setError("");
    try {
      setReport(await getEvalReport(mode));
    } catch (e) {
      setReport(null);
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadPeriodic = useCallback(async () => {
    setPeriodicLoading(true);
    try {
      setPeriodic(await getPeriodicReport());
    } catch (e) {
      setPeriodic({ ok: false, error: e instanceof Error ? e.message : "周期测试报告加载失败" });
    } finally {
      setPeriodicLoading(false);
    }
  }, []);

  const loadRag = useCallback(async () => {
    setRagLoading(true);
    try {
      setRag(await getRagReport());
    } catch (e) {
      setRag({ ok: false, error: e instanceof Error ? e.message : "RAG 客服评测报告加载失败" });
    } finally {
      setRagLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(real);
    void loadPeriodic();
    void loadRag();
  }, [real, load, loadPeriodic, loadRag]);

  async function trigger() {
    setRunning(true);
    setError("");
    try {
      const r = await runEval(real); // real 模式逐用例调 LLM 裁判，约 1 分钟
      setReport(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "跑批失败");
    } finally {
      setRunning(false);
    }
  }

  async function triggerPeriodic(saveBaseline: boolean) {
    setPeriodicRunning(true);
    try {
      const r = await runPeriodicReport(saveBaseline); // 离线确定性，秒级
      setPeriodic(r);
    } catch (e) {
      setPeriodic({ ok: false, error: e instanceof Error ? e.message : "周期测试跑批失败" });
    } finally {
      setPeriodicRunning(false);
    }
  }

  async function triggerRag(realMode: boolean) {
    setRagRunning(true);
    try {
      const r = await runRagReport(realMode); // mock 确定性秒级 / real 走 LLM 完整链路
      setRag(r);
    } catch (e) {
      setRag({ ok: false, error: e instanceof Error ? e.message : "RAG 评测跑批失败" });
    } finally {
      setRagRunning(false);
    }
  }

  const cases = report?.cases ?? [];
  const agg = report?.aggregate;
  const passCount = cases.filter((c) => c.match).length;
  const failCount = cases.length - passCount;
  const failCases = cases.filter((c) => !c.match);
  // 每类（decision/security/quality）命中统计 —— mock 门禁展示用
  const kindStats = Object.entries(
    cases.reduce<Record<string, { n: number; hit: number }>>((m, c) => {
      m[c.kind] = m[c.kind] ?? { n: 0, hit: 0 };
      m[c.kind].n += 1;
      if (c.match) m[c.kind].hit += 1;
      return m;
    }, {})
  ).map(([kind, v]) => ({ kind, ...v }));

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
        <h1 style={{ fontSize: 20, margin: 0 }}>🧪 评测中心</h1>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          {/* 评测模式切换 */}
          {[
            { v: false, label: "离线打分" },
            { v: true, label: "LLM 真实裁判" },
          ].map((m) => (
            <button
              key={String(m.v)}
              onClick={() => setReal(m.v)}
              disabled={running}
              style={{
                padding: "6px 14px",
                borderRadius: 6,
                fontSize: 13,
                border: `1px solid ${real === m.v ? "#0d6efd" : "#ddd"}`,
                background: real === m.v ? "#e7f1ff" : "#fff",
                color: real === m.v ? "#0d6efd" : "#666",
                cursor: "pointer",
              }}
            >
              {m.label}
            </button>
          ))}
          <button
            onClick={trigger}
            disabled={running || !isManager}
            title={isManager ? undefined : "仅 MANAGER 角色可运行评测"}
            style={{
              padding: "8px 20px",
              background: isManager ? "#0d6efd" : "#a0c3ff",
              color: "#fff",
              border: "none",
              borderRadius: 6,
              cursor: isManager ? "pointer" : "not-allowed",
              fontSize: 14,
            }}
          >
            {running ? (real ? "LLM 裁判跑批中（约 1 分钟）..." : "跑批中...") : "▶ 一键触发跑批"}
          </button>
        </div>
      </div>
      <p style={{ fontSize: 12, color: "#999", margin: "0 0 16px" }}>
        数据源：后端 eval harness（Golden Dataset + LLM-as-judge）·
        {report?.generated_at ? ` 最近报告生成于 ${report.generated_at} · ${MODE_LABEL[report.mode] ?? report.mode}` : " 尚未运行评测，点击右上角跑批生成报告"}
        {!isManager && " · 跑批需 MANAGER 账号（当前为 CSR，可查看报告）"}
      </p>

      {error && (
        <div style={{ ...card, padding: 20, marginBottom: 16, textAlign: "center", color: "#8a6d00", background: "#fffbe6" }}>
          {error}
          {error.includes("404") || error.includes("尚无") ? " — 切换 MANAGER 账号登录后点击「一键触发跑批」生成报告" : ""}
        </div>
      )}

      {loading && <div style={{ ...card, padding: 24, textAlign: "center", color: "#999" }}>加载中...</div>}

      {!loading && report && (
        <>
          {/* 概览 */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10, marginBottom: 16 }}>
            {[
              { label: "Golden 用例通过", value: `${passCount} / ${cases.length}`, color: failCount === 0 ? "#1a7f37" : "#dc3545" },
              report.mode === "mock"
        ? { label: "门禁", value: failCount === 0 ? "✅ 通过" : `❌ ${failCount} 未达`, color: failCount === 0 ? "#1a7f37" : "#dc3545" }
        : { label: "三维均分", value: `${agg?.total?.toFixed(2) ?? "-"} / 5`, color: "#0d6efd" },
              { label: "评测用例数", value: `${agg?.count ?? 0}`, color: "#1a7f37" },
              { label: "失败用例", value: `${failCount}`, color: failCount === 0 ? "#1a7f37" : "#dc3545" },
            ].map((k) => (
              <div key={k.label} style={{ ...card, padding: 14, textAlign: "center" }}>
                <div style={{ fontSize: 24, fontWeight: 700, color: k.color }}>{k.value}</div>
                <div style={{ fontSize: 12, color: "#777" }}>{k.label}</div>
              </div>
            ))}
          </div>

          {/* Golden 用例明细 */}
          <section style={{ ...card, marginBottom: 16 }}>
            <h2 style={{ fontSize: 15, margin: "0 0 12px" }}>
              Golden Dataset（{cases.length} 个业务边界用例）
            </h2>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ background: "#f5f6fa" }}>
                  <th style={th}>#</th>
                  <th style={th}>场景</th>
                  <th style={th}>类别</th>
                  <th style={th}>期望决策</th>
                  <th style={th}>实际</th>
                  <th style={th}>判定理由</th>
                  <th style={th}>
                    裁判评语{report.mode === "mock" ? "（离线规则判语）" : ""}
                  </th>
                  <th style={th}>结果</th>
                </tr>
              </thead>
              <tbody>
                {cases.map((c) => (
                  <tr key={c.id} style={{ borderBottom: "1px solid #eee", background: c.match ? undefined : "#fdecec" }}>
                    <td style={td}>{c.id}</td>
                    <td style={td}>{c.scenario}</td>
                    <td style={{ ...td, color: "#777" }}>{KIND_LABEL[c.kind] ?? c.kind}</td>
                    <td style={td}>{c.expected}</td>
                    <td style={td}>{c.actual}</td>
                    <td style={{ ...td, fontSize: 12, color: "#777" }}>{c.reason}</td>
                    <td style={{ ...td, fontSize: 12, color: "#555" }}>
                      {c.score.reason}
                      {c.score.confidence != null && report.mode === "real" && (
                        <span style={{ color: "#999", marginLeft: 6 }}>
                          （置信度 {(c.score.confidence * 100).toFixed(0)}%）
                        </span>
                      )}
                    </td>
                    <td style={{ ...td, fontWeight: 600, color: c.match ? "#1a7f37" : "#dc3545" }}>
                      {c.match ? "通过" : "失败"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1.2fr", gap: 16 }}>
            {/* 分数组：mock = 命中率门禁；real = LLM 裁判三维打分 */}
            <section style={card}>
              <h2 style={{ fontSize: 15, margin: "0 0 12px" }}>
                {report.mode === "mock"
                  ? "决策命中门禁（离线 · 无 LLM 计分）"
                  : `三维评分（最近跑批 · ${MODE_LABEL[report.mode] ?? report.mode}）`}
              </h2>

              {report.mode === "mock" ? (
                <div>
                  {/* 门禁结果 */}
                  <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 14 }}>
                    <div style={{ fontSize: 34, fontWeight: 700, color: failCount === 0 ? "#1a7f37" : "#dc3545" }}>
                      {passCount}
                      <span style={{ fontSize: 18, color: "#999", fontWeight: 400 }}>/{cases.length}</span>
                    </div>
                    <div style={{ fontSize: 13 }}>
                      <div style={{ fontSize: 14, fontWeight: 600, color: failCount === 0 ? "#1a7f37" : "#dc3545" }}>
                        {failCount === 0 ? "✅ 门禁通过" : `❌ 门禁未通过（${failCount} 例未达期望）`}
                      </div>
                      <div style={{ color: "#888", marginTop: 2 }}>确定性规则对 Golden 的决策命中率（不调 LLM）</div>
                    </div>
                  </div>
                  {/* 每类命中（决策边界 / 金额边界安全） */}
                  <div style={{ display: "flex", gap: 10, marginBottom: 8 }}>
                    {kindStats.map((k) => (
                      <div
                        key={k.kind}
                        style={{
                          flex: 1,
                          padding: "10px",
                          borderRadius: 8,
                          textAlign: "center",
                          background: k.hit === k.n ? "#e9f7ef" : "#fdecec",
                        }}
                      >
                        <div style={{ fontSize: 18, fontWeight: 700, color: k.hit === k.n ? "#1a7f37" : "#dc3545" }}>
                          {k.hit}/{k.n}
                        </div>
                        <div style={{ fontSize: 11, color: "#777" }}>{KIND_LABEL[k.kind] ?? k.kind}</div>
                      </div>
                    ))}
                  </div>
                  {failCases.length > 0 && (
                    <div style={{ fontSize: 12, color: "#b02a37", marginTop: 6 }}>
                      未达期望：{failCases.map((c) => `${c.id}（期望 ${c.expected}，实际 ${c.actual}）`).join("；")}
                    </div>
                  )}
                  <p style={{ fontSize: 12, color: "#999", marginTop: 10 }}>
                    离线模式依据「决策是否命中期望」给出确定性门禁，仅作回归护栏（防「修 A 场景却退化 B 场景」）；
                    质量度量请切换到「LLM 真实裁判」。
                  </p>
                </div>
              ) : (
                <>
                  {agg &&
                    [
                      { dim: "决策正确性", desc: "判得对不对", score: agg.correctness },
                      { dim: "金额边界安全", desc: "有无误放高风险单、误拒低风险单", score: agg.safety },
                      { dim: "理由质量", desc: "依据是否充分", score: agg.efficiency },
                    ].map((d) => (
                      <div key={d.dim} style={{ marginBottom: 14 }}>
                        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13, marginBottom: 4 }}>
                          <span>
                            {d.dim}
                            <span style={{ fontSize: 11, color: "#999", marginLeft: 6 }}>{d.desc}</span>
                          </span>
                          <span style={{ fontWeight: 600 }}>{d.score.toFixed(1)}</span>
                        </div>
                        <div style={{ height: 8, background: "#eee", borderRadius: 4, overflow: "hidden" }}>
                          <div
                            style={{
                              width: `${(d.score / 5) * 100}%`,
                              height: "100%",
                              background: d.score >= 4 ? "#28a745" : "#fd7e14",
                            }}
                          />
                        </div>
                      </div>
                    ))}
                  <p style={{ fontSize: 12, color: "#999", marginTop: 6 }}>
                    低于 3.5 触发回归告警；LLM 模式由裁判模型逐用例评审（置信度与评语见明细表）
                  </p>
                </>
              )}
            </section>

            {/* 报告信息 + 回归监控说明 */}
            <section style={card}>
              <h2 style={{ fontSize: 15, margin: "0 0 12px" }}>报告信息与回归监控</h2>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                <thead>
                  <tr style={{ background: "#f5f6fa" }}>
                    <th style={th}>生成时间</th>
                    <th style={th}>评测模式</th>
                    <th style={th}>通过</th>
                    <th style={th}>{report.mode === "mock" ? "门禁" : "均分"}</th>
                    <th style={th}>回归</th>
                  </tr>
                </thead>
                <tbody>
                  <tr style={{ borderBottom: "1px solid #eee" }}>
                    <td style={td}>{report.generated_at ?? "-"}</td>
                    <td style={td}>{MODE_LABEL[report.mode] ?? report.mode}</td>
                    <td style={td}>
                      {passCount} / {cases.length}
                    </td>
                    <td style={{ ...td, fontWeight: 600, color: report.mode === "mock" ? (failCount === 0 ? "#1a7f37" : "#dc3545") : "#333" }}>
                      {report.mode === "mock"
                        ? failCount === 0
                          ? "✅ 通过"
                          : `❌ ${failCount} 例未达`
                        : (agg?.total?.toFixed(2) ?? "-")}
                    </td>
                    <td style={{ ...td, fontSize: 12, color: failCount === 0 ? "#1a7f37" : "#dc3545" }}>
                      {failCount === 0 ? "无" : `${failCount} 例未达期望`}
                    </td>
                  </tr>
                </tbody>
              </table>
              <p style={{ fontSize: 12, color: "#999", marginTop: 10 }}>
                后端按模式各保留最近一次报告（离线 / 真实裁判分文件落盘）；回归监控对比历史基线，告警「修复 A 场景却退化 B 场景」（Regressive Failure）。历史趋势需后续增加报告归档查询接口。
              </p>
            </section>
          </div>
        </>
      )}

      {/* 工单8 周期测试（持续自测）：独立于 Golden 评测，始终可读 */}
      <section style={{ ...card, marginTop: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
          <h2 style={{ fontSize: 15, margin: 0 }}>📅 周期测试（工单8 持续自测）</h2>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              onClick={() => void loadPeriodic()}
              style={{ padding: "6px 12px", background: "#fff", color: "#555", border: "1px solid #ddd", borderRadius: 6, cursor: "pointer", fontSize: 12 }}
            >
              ↻ 刷新
            </button>
            {periodic?.has_report && !periodic?.has_baseline && isManager && (
              <button
                onClick={() => void triggerPeriodic(true)}
                disabled={periodicRunning}
                title="以本次结果为基线固化，供后续回归对比"
                style={{ padding: "6px 12px", background: "#6f42c1", color: "#fff", border: "none", borderRadius: 6, cursor: periodicRunning ? "wait" : "pointer", fontSize: 12 }}
              >
                {periodicRunning ? "运行中..." : "💾 固化为基线"}
              </button>
            )}
            <button
              onClick={() => void triggerPeriodic(false)}
              disabled={periodicRunning || !isManager}
              title={isManager ? "跑意图基准 + 决策 Golden（mock）+ 回归对比" : "仅 MANAGER 角色可触发"}
              style={{ padding: "6px 14px", background: isManager ? "#0d6efd" : "#a0c3ff", color: "#fff", border: "none", borderRadius: 6, cursor: isManager ? "pointer" : "not-allowed", fontSize: 12 }}
            >
              {periodicRunning ? "周期测试运行中..." : "▶ 一键触发周期测试"}
            </button>
          </div>
        </div>
        <p style={{ fontSize: 12, color: "#999", margin: "0 0 12px" }}>
          意图识别基准 + 决策 Golden（mock）+ 基线回归监控 · 验收红线 Recall≥90% / 幻觉≤2% / Token 降≥40% · 离线确定性秒级
        </p>

        {periodicLoading ? (
          <div style={{ padding: 16, textAlign: "center", color: "#999" }}>加载周期测试报告中...</div>
        ) : !periodic || !periodic.ok || !periodic.has_report ? (
          <div style={{ fontSize: 13, color: "#8a6d00", background: "#fffbe6", borderRadius: 6, padding: 12 }}>
            尚无周期测试报告 —— {periodic?.error ?? "点击右上角「一键触发周期测试」运行"}
            {!isManager && "（需 MANAGER 账号触发）"}
          </div>
        ) : (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10, marginBottom: 12 }}>
              {[
                {
                  label: "意图召回 Recall",
                  target: "≥ 90%",
                  value: `${((periodic.Recall ?? 0) * 100).toFixed(1)}%`,
                  ok: periodic.red_lines?.recall_pass,
                },
                {
                  label: "幻觉率",
                  target: "≤ 2%",
                  value: `${((periodic.HallucinationRate ?? 0) * 100).toFixed(1)}%`,
                  ok: periodic.red_lines?.hallucination_pass,
                },
                {
                  label: "Token 降幅（混合 vs 纯 LLM）",
                  target: "≥ 40%",
                  value: `${((periodic.TokenReduction ?? 0) * 100).toFixed(1)}%`,
                  ok: periodic.red_lines?.token_reduction_pass,
                },
              ].map((k) => (
                <div key={k.label} style={{ padding: "12px", borderRadius: 8, background: k.ok ? "#e9f7ef" : "#fdecec", textAlign: "center" }}>
                  <div style={{ fontSize: 22, fontWeight: 700, color: k.ok ? "#1a7f37" : "#dc3545" }}>{k.value}</div>
                  <div style={{ fontSize: 12, color: "#555" }}>{k.label}</div>
                  <div style={{ fontSize: 11, color: "#999", marginTop: 2 }}>
                    目标 {k.target} · {k.ok ? "✅ 达标" : "❌ 未达标"}
                  </div>
                </div>
              ))}
            </div>

            {/* 红线总判定 */}
            <div
              style={{
                padding: "10px 12px",
                borderRadius: 8,
                marginBottom: 10,
                fontSize: 13,
                background: periodic.red_lines?.all_pass ? "#e9f7ef" : "#fdecec",
                color: periodic.red_lines?.all_pass ? "#1a7f37" : "#b02a37",
              }}
            >
              {periodic.red_lines?.all_pass
                ? "✅ 全部验收红线达标"
                : `❌ ${(periodic.red_lines?.fails ?? []).join("；") || "存在红线未达标"}`}
            </div>

            {/* 基线回归 */}
            <div style={{ fontSize: 12, color: "#555", marginBottom: 10 }}>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>基线回归（{periodic.has_baseline ? "有基线" : "无基线"}）</div>
              <ul style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 2 }}>
                {(periodic.regression_notes ?? ["-"]).map((n) => (
                  <li key={n} style={{ color: n.includes("劣化") ? "#b02a37" : "#777" }}>{n}</li>
                ))}
              </ul>
            </div>

            {/* 元信息 */}
            <div style={{ fontSize: 11, color: "#999", display: "flex", flexWrap: "wrap", gap: 14, borderTop: "1px solid #eee", paddingTop: 8 }}>
              <span>样本 {periodic.samples} 个（模板自动生成）</span>
              <span>规则命中率 {((periodic.rule_hit_rate ?? 0) * 100).toFixed(1)}%</span>
              <span>TTFT 降幅 {((periodic.TTFTReduction ?? 0) * 100).toFixed(1)}%</span>
              <span>最近运行 {periodic.generated_at ?? "-"}</span>
            </div>
          </>
        )}
      </section>

      {/* 买家侧 RAG 客服评测（5 维指标 + 红线） */}
      <section style={{ ...card, marginTop: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
          <h2 style={{ fontSize: 15, margin: 0 }}>🤖 买家侧 RAG 客服评测（20 条回归用例）</h2>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <button
              onClick={() => void loadRag()}
              style={{ padding: "6px 12px", background: "#fff", color: "#555", border: "1px solid #ddd", borderRadius: 6, cursor: "pointer", fontSize: 12 }}
            >
              ↻ 刷新
            </button>
            <button
              onClick={() => void triggerRag(false)}
              disabled={ragRunning || !isManager}
              title={isManager ? "跑 20 条用例 × 5 维指标（Recall/答案/拒答/路由/幻觉）" : "仅 MANAGER 角色可触发"}
              style={{ padding: "6px 14px", background: isManager ? "#0d6efd" : "#a0c3ff", color: "#fff", border: "none", borderRadius: 6, cursor: isManager ? "pointer" : "not-allowed", fontSize: 12 }}
            >
              {ragRunning ? "RAG 评测运行中..." : "▶ 一键触发 RAG 评测"}
            </button>
          </div>
        </div>
        <p style={{ fontSize: 12, color: "#999", margin: "0 0 12px" }}>
          知识库 docs/客服知识库.md（v1.0）· 17 政策问答 + 2 安全红线（零容忍）+ 1 双通道路由 · 默认 mock 确定性原文路径（秒级），LLM 完整链路需配置 LLM_API_KEY
        </p>

        {ragLoading ? (
          <div style={{ padding: 16, textAlign: "center", color: "#999" }}>加载 RAG 评测报告中...</div>
        ) : !rag || !rag.ok || !rag.has_report ? (
          <div style={{ fontSize: 13, color: "#8a6d00", background: "#fffbe6", borderRadius: 6, padding: 12 }}>
            尚无 RAG 客服评测报告 —— {rag?.error ?? "点击右上角「一键触发 RAG 评测」运行"}
            {!isManager && "（需 MANAGER 账号触发）"}
          </div>
        ) : (
          <>
            {/* 五维指标卡 */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 10, marginBottom: 12 }}>
              {[
                {
                  label: "Recall@3",
                  target: "≥ 90%",
                  value: `${((rag.aggregate?.Recall ?? 0) * 100).toFixed(1)}%`,
                  sub: `${rag.aggregate?.recall_n}/${rag.aggregate?.policy_total}`,
                  ok: rag.red_lines?.recall_pass,
                },
                {
                  label: "答案正确率",
                  target: "≥ 90%",
                  value: `${((rag.aggregate?.AnswerRate ?? 0) * 100).toFixed(1)}%`,
                  sub: `${rag.aggregate?.answer_n}/${rag.aggregate?.policy_total}`,
                  ok: rag.red_lines?.answer_pass,
                },
                {
                  label: "拒答正确率",
                  target: "100% 零容忍",
                  value: `${((rag.aggregate?.RefuseRate ?? 0) * 100).toFixed(1)}%`,
                  sub: `${rag.aggregate?.refuse_n}/${rag.aggregate?.refuse_total}`,
                  ok: rag.red_lines?.refuse_pass,
                },
                {
                  label: "路由准确率",
                  target: "100%",
                  value: `${((rag.aggregate?.RouteRate ?? 0) * 100).toFixed(1)}%`,
                  sub: `${rag.aggregate?.route_n}/${rag.aggregate?.route_total}`,
                  ok: rag.red_lines?.route_pass,
                },
                {
                  label: "幻觉率",
                  target: "≤ 2%",
                  value: `${((rag.aggregate?.HallucinationRate ?? 0) * 100).toFixed(1)}%`,
                  sub: `${rag.aggregate?.halluc_n} 例`,
                  ok: rag.red_lines?.hallucination_pass,
                },
              ].map((k) => (
                <div key={k.label} style={{ padding: "12px", borderRadius: 8, background: k.ok ? "#e9f7ef" : "#fdecec", textAlign: "center" }}>
                  <div style={{ fontSize: 22, fontWeight: 700, color: k.ok ? "#1a7f37" : "#dc3545" }}>{k.value}</div>
                  <div style={{ fontSize: 12, color: "#555" }}>{k.label}</div>
                  <div style={{ fontSize: 11, color: "#999", marginTop: 2 }}>
                    {k.sub} · 目标 {k.target} · {k.ok ? "✅ 达标" : "❌ 未达标"}
                  </div>
                </div>
              ))}
            </div>

            {/* 红线总判定 */}
            <div
              style={{
                padding: "10px 12px",
                borderRadius: 8,
                marginBottom: 12,
                fontSize: 13,
                background: rag.red_lines?.all_pass ? "#e9f7ef" : "#fdecec",
                color: rag.red_lines?.all_pass ? "#1a7f37" : "#b02a37",
              }}
            >
              {rag.red_lines?.all_pass
                ? "✅ 全部红线达标（Recall≥90% / 答案≥90% / 拒答零容忍 / 路由100% / 幻觉≤2%）"
                : `❌ ${(rag.red_lines?.fails ?? []).join("；") || "存在红线未达标"}`}
            </div>

            {/* 逐用例明细 */}
            <h3 style={{ fontSize: 13, margin: "0 0 8px" }}>逐用例判定</h3>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ background: "#f5f6fa" }}>
                  <th style={th}>#</th>
                  <th style={th}>场景</th>
                  <th style={th}>类型</th>
                  <th style={th}>期望命中</th>
                  <th style={th}>实际 top3</th>
                  <th style={th}>要点覆盖</th>
                  <th style={th}>幻觉</th>
                  <th style={th}>结果</th>
                </tr>
              </thead>
              <tbody>
                {(rag.cases ?? []).map((c) => (
                  <tr key={c.id} style={{ borderBottom: "1px solid #eee", background: c.pass ? undefined : "#fdecec" }}>
                    <td style={td}>{c.id}</td>
                    <td style={td}>{c.scenario}</td>
                    <td style={{ ...td, color: "#777" }}>
                      {c.kind === "policy" ? "政策问答" : c.kind === "refuse" ? "红线拒答" : "双通道路由"}
                    </td>
                    <td style={td}>{(c.expected ?? []).join(" / ") || "—"}</td>
                    <td style={{ ...td, fontSize: 11, color: "#555" }}>
                      {c.top3 ? c.top3.join(" / ") : c.kind === "refuse" ? `reason=${c.reason}` : `resp=${c.resp_type}`}
                    </td>
                    <td style={{ ...td, fontSize: 11, color: "#555" }}>
                      {c.answer_pass === false
                        ? `❌ 缺 ${(c.missing_keywords ?? []).join("、") || "-"}`
                        : c.answer_pass === true
                          ? "✅"
                          : c.kind === "refuse"
                            ? (c.reason === c.expected_reason ? "✅" : "❌")
                            : "—"}
                    </td>
                    <td style={{ ...td, fontSize: 11 }}>
                      {c.hallucinations && c.hallucinations.length > 0 ? (
                        <span style={{ color: "#b02a37" }}>⚠ {c.hallucinations.join("、")}</span>
                      ) : (
                        <span style={{ color: "#aaa" }}>无</span>
                      )}
                    </td>
                    <td style={{ ...td, fontWeight: 600, color: c.pass ? "#1a7f37" : "#dc3545" }}>
                      {c.pass ? "✅" : "❌"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p style={{ fontSize: 11, color: "#999", marginTop: 8 }}>
              mock 模式答案 = top-3 命中条目知识库原文拼接，幻觉率结构性为 0；LLM 完整链路（real）由后端真实检测。
              安全红线 KB18/19 零容忍：套问内部阈值 / 诱导越权执行退款必须拒答。
            </p>
          </>
        )}
      </section>
    </div>
  );
}
