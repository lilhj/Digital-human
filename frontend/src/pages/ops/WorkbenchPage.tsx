/** 决策工作台：概览大屏 + 案件列表（状态筛选）+ 外部渠道代录（SSE 实时刷新）。 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type { CaseListResponse, CaseSummary, Overview } from "../../api/types";
import CreateCaseForm from "../../components/CreateCaseForm";
import StatusBadge from "../../components/StatusBadge";
import { card, td, th } from "../../theme";

const STATUS_FILTERS = ["", "SUSPENDED", "RUNNING", "COMPLETED", "REJECTED", "FAILED"];

export default function WorkbenchPage() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [apiDown, setApiDown] = useState(false);

  async function load() {
    const query = filter ? `?status=${filter}` : "";
    const [ov, list] = await Promise.all([
      api.get<Overview>("/dashboard/overview"),
      api.get<CaseListResponse>(`/cases${query}`),
    ]);
    setOverview(ov);
    setCases(list.items);
    setApiDown(false);
  }

  useEffect(() => {
    setLoading(true);
    load().catch(() => setApiDown(true)).finally(() => setLoading(false));
    // SSE 订阅（token 走查询参数，EventSource 无法带自定义 header）
    const token = localStorage.getItem("refund_token") ?? "";
    const es = new EventSource(`/api/v1/cases/0/events?token=${encodeURIComponent(token)}`);
    es.onmessage = () => load().catch(() => {});
    es.onerror = () => {};
    return () => es.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: 24 }}>
      <h1 style={{ fontSize: 20, margin: "0 0 16px" }}>🛡 决策工作台</h1>

      {apiDown && (
        <div style={{ ...card, borderColor: "#ffc107", background: "#fffdf5", marginBottom: 16, fontSize: 13, color: "#8a6d00" }}>
          后端 API 未连接（FastAPI :8000 未启动）。页面已渲染，数据待后端启动后自动加载。
        </div>
      )}

      {/* 大屏概览 */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 10, marginBottom: 20 }}>
        {[
          ["总工单", overview?.total ?? "-"],
          ["挂起待审批", overview?.suspended ?? "-"],
          ["今日创建", overview?.today_created ?? "-"],
          ["今日完成", overview?.today_completed ?? "-"],
          ["高风险", overview?.risk_distribution.high ?? "-"],
        ].map(([label, value]) => (
          <div key={label} style={{ ...card, padding: 14, textAlign: "center" }}>
            <div style={{ fontSize: 24, fontWeight: 700, color: "#0d6efd" }}>{value}</div>
            <div style={{ fontSize: 12, color: "#777" }}>{label}</div>
          </div>
        ))}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "380px 1fr", gap: 16 }}>
        <CreateCaseForm onCreated={() => load().catch(() => {})} />

        <div>
          <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
            {STATUS_FILTERS.map((s) => (
              <button
                key={s}
                onClick={() => setFilter(s)}
                style={{
                  padding: "5px 12px",
                  borderRadius: 14,
                  border: filter === s ? "2px solid #0d6efd" : "1px solid #ccc",
                  background: filter === s ? "#e7f1ff" : "#fff",
                  cursor: "pointer",
                  fontSize: 13,
                }}
              >
                {s === "" ? "全部" : s}
              </button>
            ))}
          </div>

          {loading ? (
            <p style={{ color: "#888" }}>加载中...</p>
          ) : cases.length === 0 ? (
            <p style={{ color: "#888" }}>暂无案件</p>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse", background: "#fff", fontSize: 13 }}>
              <thead>
                <tr style={{ background: "#f5f6fa" }}>
                  <th style={th}>工单号</th>
                  <th style={th}>订单号</th>
                  <th style={th}>金额</th>
                  <th style={th}>风险分</th>
                  <th style={th}>状态</th>
                  <th style={th}>操作</th>
                </tr>
              </thead>
              <tbody>
                {cases.map((c) => (
                  <tr key={c.id} style={{ borderBottom: "1px solid #eee" }}>
                    <td style={td}>{c.ticket_no}</td>
                    <td style={td}>{c.order_id}</td>
                    <td style={td}>¥{(c.applicant_amount / 100).toFixed(2)}</td>
                    <td style={td}>{c.risk_score?.toFixed(2) ?? "-"}</td>
                    <td style={td}><StatusBadge status={c.status} /></td>
                    <td style={td}>
                      <Link to={`/cases/${c.id}`} style={{ color: "#0d6efd" }}>查看 →</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
