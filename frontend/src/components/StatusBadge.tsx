/** 工单状态徽章：状态 → 颜色 + 中文标签。 */
const MAP: Record<string, { label: string; color: string; bg: string }> = {
  RUNNING: { label: "处理中", color: "#0d6efd", bg: "#e7f1ff" },
  SUSPENDED: { label: "挂起待审批", color: "#8a6d00", bg: "#fff8e1" },
  APPROVED: { label: "已批准", color: "#1a7f37", bg: "#e6f4ea" },
  REJECTED: { label: "已拒绝", color: "#b02a37", bg: "#fdecec" },
  COMPLETED: { label: "已完成", color: "#1a7f37", bg: "#e6f4ea" },
  FAILED: { label: "失败", color: "#b02a37", bg: "#fdecec" },
  PENDING: { label: "待处理", color: "#555", bg: "#f0f0f0" },
};

export default function StatusBadge({ status }: { status: string }) {
  const s = MAP[status] ?? { label: status, color: "#555", bg: "#f0f0f0" };
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 10px",
        borderRadius: 12,
        fontSize: 12,
        fontWeight: 600,
        color: s.color,
        background: s.bg,
      }}
    >
      {s.label}
    </span>
  );
}
