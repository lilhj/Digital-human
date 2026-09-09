/** 决策流三态流转：后端案件八态 → 宏观三态（运行中 → 挂起中 → 已完成）。
 *  员工端 StageStepper 与买家端「处理中/挂起待处理/已完成」共用同一套映射，
 *  与 backend/app/domain/status.py 的 CaseStatus 枚举 + TRANSITIONS 对齐。
 *
 *  映射口径：
 *    RUNNING     = CREATED / RUNNING / APPROVED / REFUNDING / REFUND_FAILED（流水线在跑，含退款重试）
 *    SUSPENDED   = SUSPENDED（等人工审批）
 *    COMPLETED   = COMPLETED / REJECTED / FAILED（终态：已退款 / 已拒单 / 流程终止）
 */
export type CaseStage = "RUNNING" | "SUSPENDED" | "COMPLETED";

const STAGE_MAP: Record<string, CaseStage> = {
  CREATED: "RUNNING",
  RUNNING: "RUNNING",
  APPROVED: "RUNNING",
  REFUNDING: "RUNNING",
  REFUND_FAILED: "RUNNING",
  SUSPENDED: "SUSPENDED",
  COMPLETED: "COMPLETED",
  REJECTED: "COMPLETED",
  FAILED: "COMPLETED",
};

/** 买家端通俗文案（与三态一一对应）。 */
export const CASE_STAGE_LABEL: Record<CaseStage, string> = {
  RUNNING: "处理中",
  SUSPENDED: "挂起待处理",
  COMPLETED: "已完成",
};

export function caseStage(status: string): CaseStage {
  return STAGE_MAP[status] ?? "RUNNING";
}
