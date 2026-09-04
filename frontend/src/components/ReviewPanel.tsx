/** 人工审核面板（主管）：一键同意/拒绝 + 意见，按钮按状态与角色可用。 */
import { useState } from "react";
import { api, getRole } from "../api/client";
import type { CaseDetail } from "../api/types";

export default function ReviewPanel({
  detail,
  onDone,
}: {
  detail: CaseDetail;
  onDone: () => void;
}) {
  const [comment, setComment] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const isManager = getRole() === "MANAGER";
  const canReview = isManager && detail.status === "SUSPENDED";

  async function decide(action: "APPROVE" | "REJECT") {
    setLoading(true);
    setError("");
    try {
      await api.post(`/cases/${detail.id}/decision`, { action, comment });
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  if (!canReview) return null;

  return (
    <div style={{ border: "1px solid #ffc107", borderRadius: 8, padding: 16, background: "#fffdf5" }}>
      <h3 style={{ margin: "0 0 8px", fontSize: 15, color: "#8a6d00" }}>
        ⚠ 人工审批断点（Human-in-the-loop）
      </h3>
      <textarea
        value={comment}
        onChange={(e) => setComment(e.target.value)}
        placeholder="请输入审批意见（如：情况属实，批准退款）"
        rows={2}
        style={{ width: "100%", padding: 8, borderRadius: 6, border: "1px solid #ccc", boxSizing: "border-box" }}
      />
      <div style={{ marginTop: 10, display: "flex", gap: 10 }}>
        <button
          onClick={() => decide("APPROVE")}
          disabled={loading}
          style={{ padding: "8px 20px", background: "#28a745", color: "#fff", border: "none", borderRadius: 6, cursor: "pointer" }}
        >
          同意退款
        </button>
        <button
          onClick={() => decide("REJECT")}
          disabled={loading}
          style={{ padding: "8px 20px", background: "#dc3545", color: "#fff", border: "none", borderRadius: 6, cursor: "pointer" }}
        >
          拒绝退款
        </button>
      </div>
      {error && <p style={{ color: "#dc3545", fontSize: 13, marginTop: 8 }}>{error}</p>}
    </div>
  );
}
