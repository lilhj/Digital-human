/** 决策流三态流转 Stepper：运行中 → 挂起中 → 已完成。
 *  宏观生命周期视图，与 CaseDetailPage 下方 AgentFlow 微观节点时间线互补。
 *  - 挂起中为当前态时复用 blink 动画闪烁，提示等待主管人工审批；
 *  - 终态单若未走过人工节点（如订单三查硬闸自动拒），挂起中显示「跳过」，如实反映自动直通路径；
 *  - 审批通过后恢复执行（APPROVED/REFUNDING 仍在运行态）但已挂起过 → 挂起中显示「已走人工」。
 */
import { Fragment } from "react";
import type { AgentNode } from "../api/types";
import { caseStage, type CaseStage } from "../api/caseStage";

const STAGES: { key: CaseStage; label: string; sub: string }[] = [
  { key: "RUNNING", label: "运行中", sub: "Agent 流水线执行中" },
  { key: "SUSPENDED", label: "挂起中", sub: "等待主管人工审批" },
  { key: "COMPLETED", label: "已完成", sub: "退款 / 拒单 / 终态" },
];

type StepState = "current" | "passed" | "future" | "skipped";

const ORDER: Record<CaseStage, number> = { RUNNING: 0, SUSPENDED: 1, COMPLETED: 2 };

function stepState(key: CaseStage, current: CaseStage, hasHuman: boolean): StepState {
  if (key === current) return "current";
  // 终态但从未挂起（自动直通路径）→ 挂起中如实显示「跳过」
  if (key === "SUSPENDED" && current === "COMPLETED" && !hasHuman) return "skipped";
  // 挂起过（含审批后恢复继续跑：当前在运行态但已走过人工）
  if (key === "SUSPENDED" && hasHuman) return "passed";
  return ORDER[key] < ORDER[current] ? "passed" : "future";
}

export default function StageStepper({
  status,
  nodes = [],
}: {
  status: string;
  nodes?: AgentNode[];
}) {
  const current = caseStage(status);
  const hasHuman = nodes.some((n) => n.agent === "HUMAN_REVIEW");

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
      {STAGES.map((s, i) => {
        const st = stepState(s.key, current, hasHuman);
        const isLast = i === STAGES.length - 1;
        const blink = st === "current" && current === "SUSPENDED";
        const filled = st === "current" || st === "passed";
        const bg =
          st === "current"
            ? current === "SUSPENDED"
              ? "#ffc107"
              : current === "COMPLETED"
                ? "#28a745"
                : "#0d6efd"
            : st === "passed"
              ? "#28a745"
              : "#fff";
        const border =
          st === "skipped" ? "2px dashed #c0c0c0" : st === "future" ? "2px solid #d0d0d0" : "none";
        const fg =
          st === "current"
            ? current === "SUSPENDED"
              ? "#333"
              : "#fff"
            : st === "passed"
              ? "#fff"
              : "#b0b0b0";
        const labelColor =
          st === "current"
            ? current === "SUSPENDED"
              ? "#8a6d00"
              : current === "COMPLETED"
                ? "#1a7f37"
                : "#0d6efd"
            : st === "passed"
              ? "#28a745"
              : "#aaa";
        return (
          <Fragment key={s.key}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, opacity: st === "skipped" ? 0.6 : 1 }}>
              <div
                style={{
                  width: 26,
                  height: 26,
                  borderRadius: "50%",
                  background: bg,
                  border,
                  color: fg,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: 13,
                  fontWeight: 700,
                  flexShrink: 0,
                  animation: blink ? "blink 1s infinite" : undefined,
                }}
              >
                {st === "current" && current === "SUSPENDED" ? "⏸" : filled ? "✓" : "·"}
              </div>
              <div>
                <div style={{ fontSize: 14, fontWeight: 600, color: labelColor }}>{s.label}</div>
                <div style={{ fontSize: 11, color: st === "skipped" ? "#bbb" : "#999" }}>
                  {st === "skipped" ? "未走人工（自动直通）" : s.sub}
                </div>
              </div>
            </div>
            {!isLast && (
              <div
                style={{ flex: 1, minWidth: 24, height: 2, margin: "13px 12px 0", background: "#e0e0e0" }}
              />
            )}
          </Fragment>
        );
      })}
      <style>{`@keyframes blink { 50% { opacity: 0.35; } }`}</style>
    </div>
  );
}
