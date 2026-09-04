/** 批量审批（主管/管理员）：三段式排版——
 *  ① 挂起单快照列表（哪些单待批量处理）
 *  ② 导出挂起订单 / 上传审批结果（Excel 离线处理 + 沙箱模式徽章）
 *  ③ 审批结果汇总（批准/拒绝/跳过/重复/失败 + 逐行明细）
 */
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type { CaseListResponse, CaseSummary } from "../../api/types";
import { card, td, th } from "../../theme";

interface BatchSummary {
  sandbox_mode: string;
  summary: {
    approved: number;
    rejected: number;
    skipped: number;
    duplicated: number;
    failed: number;
    invalid: number;
    total: number;
  };
  rows: { case_id: number; status: string; message: string }[];
}

export default function BatchApprovePage() {
  const [suspended, setSuspended] = useState<CaseSummary[]>([]);
  const [batchMode, setBatchMode] = useState<string>("");
  const [batchSummary, setBatchSummary] = useState<BatchSummary | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [apiDown, setApiDown] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  async function loadSuspended() {
    const list = await api.get<CaseListResponse>("/cases?status=SUSPENDED");
    setSuspended(list.items);
    setApiDown(false);
  }

  useEffect(() => {
    loadSuspended().catch(() => setApiDown(true));
    api.get<{ sandbox_mode: string }>("/batch/sandbox-mode")
      .then((r) => setBatchMode(r.sandbox_mode))
      .catch(() => setBatchMode("off"));
  }, []);

  async function exportSuspended() {
    setError("");
    try {
      const resp = await fetch("/api/v1/batch/export", {
        headers: { Authorization: `Bearer ${localStorage.getItem("refund_token")}` },
      });
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        throw new Error(data.message ?? "导出失败");
      }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const cd = resp.headers.get("Content-Disposition") ?? "";
      const m = cd.match(/filename="?([^";]+)"?/);
      a.download = m ? m[1] : "suspended.xlsx";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function uploadApproval(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setError("");
    setBatchSummary(null);
    setLoading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const resp = await fetch("/api/v1/batch/approve", {
        method: "POST",
        headers: { Authorization: `Bearer ${localStorage.getItem("refund_token")}` },
        body: form,
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.message ?? "批量审批失败");
      setBatchSummary(data);
      loadSuspended().catch(() => {});
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: 24 }}>
      <h1 style={{ fontSize: 20, margin: "0 0 16px" }}>📦 批量审批（Excel 离线处理）</h1>

      {apiDown && (
        <div style={{ ...card, borderColor: "#ffc107", background: "#fffdf5", marginBottom: 16, fontSize: 13, color: "#8a6d00" }}>
          后端 API 未连接（FastAPI :8000 未启动），挂起单列表与导出/上传暂不可用。
        </div>
      )}

      {/* ① 挂起单快照 */}
      <section style={{ ...card, marginBottom: 16 }}>
        <h2 style={{ fontSize: 15, margin: "0 0 10px" }}>
          ① 挂起待审批快照 <span style={{ fontSize: 12, color: "#888", fontWeight: 400 }}>（{suspended.length} 单，导出 Excel 即此范围）</span>
        </h2>
        {suspended.length === 0 ? (
          <p style={{ fontSize: 13, color: "#888" }}>当前无挂起单</p>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ background: "#f5f6fa" }}>
                <th style={th}>工单号</th>
                <th style={th}>订单号</th>
                <th style={th}>金额</th>
                <th style={th}>风险分</th>
                <th style={th}>舆情</th>
                <th style={th}>描述摘要</th>
                <th style={th}>操作</th>
              </tr>
            </thead>
            <tbody>
              {suspended.map((c) => (
                <tr key={c.id} style={{ borderBottom: "1px solid #eee" }}>
                  <td style={td}>{c.ticket_no}</td>
                  <td style={td}>{c.order_id}</td>
                  <td style={td}>¥{(c.applicant_amount / 100).toFixed(2)}</td>
                  <td style={{ ...td, fontWeight: 600, color: riskColor(c.risk_score) }}>
                    {c.risk_score?.toFixed(2) ?? "-"}
                  </td>
                  <td style={td}>{sentimentLabel(c.sentiment_score)}</td>
                  <td style={{ ...td, maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "#777" }}>
                    {c.description || "-"}
                  </td>
                  <td style={td}><Link to={`/cases/${c.id}`} style={{ color: "#0d6efd" }}>查看 →</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* ② 导出 / 上传 */}
      <section style={{ ...card, marginBottom: 16, background: "#fbfdff" }}>
        <h2 style={{ fontSize: 15, margin: "0 0 10px" }}>② 离线审批操作</h2>
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <span style={{ fontSize: 12, color: "#777" }}>
            沙箱模式：<b>{batchMode || "-"}</b>
          </span>
          <button onClick={exportSuspended} style={{ padding: "7px 16px", background: "#0d6efd", color: "#fff", border: "none", borderRadius: 6, cursor: "pointer" }}>
            导出挂起订单
          </button>
          <label style={{ cursor: "pointer", padding: "7px 16px", background: "#6f42c1", color: "#fff", borderRadius: 6, fontSize: 14 }}>
            {loading ? "审批中..." : "上传审批结果"}
            <input ref={fileRef} type="file" accept=".xlsx" style={{ display: "none" }} onChange={uploadApproval} />
          </label>
        </div>
        <p style={{ fontSize: 12, color: "#999", margin: "10px 0 0" }}>
          流程：导出 Excel → 离线填写审批列 → 上传 → 系统逐行执行（沙箱隔离处理，幂等去重）
        </p>
        {error && <p style={{ color: "#dc3545", fontSize: 13, marginTop: 8 }}>{error}</p>}
      </section>

      {/* ③ 审批结果汇总（三卡片 + 失败明细，部分失败逐条汇总不整体回滚） */}
      {batchSummary && (
        <section style={card}>
          <h2 style={{ fontSize: 15, margin: "0 0 12px" }}>③ 本次审批结果</h2>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12, marginBottom: 12 }}>
            <div style={{ border: "1px solid #e6f4ea", background: "#f6fcf8", borderRadius: 8, padding: 14, textAlign: "center" }}>
              <div style={{ fontSize: 26, fontWeight: 700, color: "#1a7f37" }}>{batchSummary.summary.approved}</div>
              <div style={{ fontSize: 12, color: "#777" }}>批准</div>
            </div>
            <div style={{ border: "1px solid #fdecec", background: "#fef7f7", borderRadius: 8, padding: 14, textAlign: "center" }}>
              <div style={{ fontSize: 26, fontWeight: 700, color: "#dc3545" }}>{batchSummary.summary.rejected}</div>
              <div style={{ fontSize: 12, color: "#777" }}>拒绝</div>
            </div>
            <div style={{ border: "1px solid #fff3cd", background: "#fffdf5", borderRadius: 8, padding: 14, textAlign: "center" }}>
              <div style={{ fontSize: 26, fontWeight: 700, color: "#8a6d3b" }}>
                {batchSummary.summary.failed + batchSummary.summary.invalid + batchSummary.summary.skipped + batchSummary.summary.duplicated}
              </div>
              <div style={{ fontSize: 12, color: "#777" }}>
                失败/异常（失败 {batchSummary.summary.failed} · 无效 {batchSummary.summary.invalid} · 跳过 {batchSummary.summary.skipped} · 重复 {batchSummary.summary.duplicated}）
              </div>
            </div>
          </div>
          <div style={{ fontSize: 12, color: "#555", maxHeight: 180, overflowY: "auto" }}>
            <div style={{ marginBottom: 6, color: "#888" }}>逐行明细（共 {batchSummary.summary.total} 行，部分失败不回滚）：</div>
            {batchSummary.rows.map((r, i) => (
              <div key={i}>case {r.case_id}: {r.status} — {r.message}</div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

/** 风险分红绿分级：≤0.2 绿（低）/ 0.2~0.5 橙（中）/ >0.5 红（高） */
function riskColor(score: number | null): string {
  if (score == null) return "#888";
  if (score > 0.5) return "#dc3545";
  if (score > 0.2) return "#fd7e14";
  return "#1a7f37";
}

/** 舆情等级：≥0.6 HIGH / ≥0.3 MEDIUM / 其余 LOW */
function sentimentLabel(score: number | null): string {
  if (score == null) return "-";
  if (score >= 0.6) return "HIGH";
  if (score >= 0.3) return "MEDIUM";
  return "LOW";
}
