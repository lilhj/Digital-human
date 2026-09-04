/** 全局共享样式常量：保持与工单1 现有风格一致（内联样式、#0d6efd 主色、白卡片 + 浅灰底）。 */
import type { CSSProperties } from "react";

export const PRIMARY = "#0d6efd";
export const DANGER = "#dc3545";
export const SUCCESS = "#28a745";
export const WARNING = "#ffc107";
export const BG = "#f5f6fa";

export const card: CSSProperties = {
  background: "#fff",
  border: "1px solid #eee",
  borderRadius: 8,
  padding: 16,
};

export const input: CSSProperties = {
  width: "100%",
  padding: 8,
  borderRadius: 6,
  border: "1px solid #ccc",
  marginTop: 4,
  boxSizing: "border-box",
};

export const btnPrimary: CSSProperties = {
  padding: "8px 20px",
  background: PRIMARY,
  color: "#fff",
  border: "none",
  borderRadius: 6,
  cursor: "pointer",
  fontSize: 14,
};

export const btnGhost: CSSProperties = {
  padding: "8px 20px",
  background: "#fff",
  color: "#333",
  border: "1px solid #ccc",
  borderRadius: 6,
  cursor: "pointer",
  fontSize: 14,
};

export const th: CSSProperties = { padding: "8px 10px", textAlign: "left", fontSize: 12 };
export const td: CSSProperties = { padding: "8px 10px" };

/** 金额（分 → 元字符串） */
export function fen(cents: number): string {
  return `¥${(cents / 100).toFixed(2)}`;
}
