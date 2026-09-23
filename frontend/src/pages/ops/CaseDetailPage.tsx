/** 单 case 详情（对标信息架构 §6.3，左右分栏）：
 *  左栏：case 概要（买家脱敏）+ 买家诉求/凭证 + 系统判定 + 主管审批操作。
 *  右栏：决策链路 trace 纵向节点时间线（标注能力来源：工单6/工单8/v2.0/工单5）+ 顶部汇总条。
 *  未接入节点（Critic/DLP/意图/三查）灰显，后端落地后自动点亮。
 */
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../../api/client";
import type { AgentNode, CaseDetail, CaseGraph } from "../../api/types";
import ReviewPanel from "../../components/ReviewPanel";
import StatusBadge from "../../components/StatusBadge";
import StageStepper from "../../components/StageStepper";
import { card } from "../../theme";

/* ---------- 买家标识脱敏（DLP 体现：全程打码显示） ---------- */
function maskApplicant(id: string): string {
  if (/^1\d{10}$/.test(id)) return `${id.slice(0, 3)}****${id.slice(7)}`;
  if (id.length > 4) return `${id.slice(0, 2)}***${id.slice(-2)}`;
  return "****";
}

/* ---------- 工单8 意图识别标签/配色（对齐大屏） ---------- */
const INTENT_LABEL: Record<string, string> = {
  REFUND: "退款",
  RETURN: "退货",
  EXCHANGE: "换货",
  UNKNOWN: "兜底转人工",
};
const INTENT_COLOR: Record<string, string> = {
  REFUND: "#0d6efd",
  RETURN: "#6f42c1",
  EXCHANGE: "#20c997",
  UNKNOWN: "#fd7e14",
};
const SOURCE_LABEL: Record<string, string> = {
  rule: "规则（Node A）",
  llm: "LLM（Node B）",
  fallback: "兜底降级",
  fake: "测试",
};
/* 凭证一致性独立信号（从 fraud_score 拆出，规则优先 LLM 兜底）展示映射 */
const CONSISTENCY_META: Record<string, { label: string; color: string }> = {
  MATCH: { label: "✓ 一致（描述与凭证相符）", color: "#28a745" },
  PARTIAL: { label: "⚠ 部分相符（存在出入）", color: "#fd7e14" },
  MISMATCH: { label: "✗ 不符（描述与凭证矛盾）", color: "#dc3545" },
  UNCERTAIN: { label: "? 信息不足", color: "#868e96" },
};

/* ---------- 节点时间线定义（§6.3 链路顺序 + 能力来源标注） ---------- */
interface Slot {
  key: string;
  label: string;
  source: string; // 能力来源
  agents: string[]; // 对应后端 AgentRun.agent_name（空 = 未接入）
}

const SLOTS: Slot[] = [
  { key: "intake", label: "Intake 接入", source: "工单1", agents: ["INTAKE"] },
  { key: "critic", label: "Critic 注入检测", source: "工单6", agents: ["CRITIC"] },
  { key: "dlp", label: "DLP 数据脱敏", source: "工单6", agents: ["DLP"] },
  { key: "intent", label: "意图识别（双层）", source: "工单8", agents: ["INTENT"] },
  { key: "verify", label: "订单三查", source: "v2.0", agents: ["ORDER_VERIFY"] },
  { key: "evidence", label: "证据 OCR 识别", source: "工单1", agents: ["EVIDENCE"] },
  { key: "risk", label: "风控 + 舆情（合并）", source: "工单5", agents: ["FRAUD", "SENTIMENT"] },
  { key: "decision", label: "Decision 决策", source: "工单1", agents: ["DECISION"] },
  { key: "human", label: "挂起人工审批", source: "当前", agents: ["HUMAN_REVIEW"] },
];

type SlotState = "done" | "current" | "failed" | "pending" | "skipped";

function slotState(slot: Slot, nodeMap: Map<string, AgentNode>, caseStatus: string): SlotState {
  const runs = slot.agents.map((a) => nodeMap.get(a)).filter(Boolean) as AgentNode[];
  // 工单6 安全网关短路：intake 里 Critic 命中 BLOCK（越权/注入）→
  // 意图/三查/风控等中段节点被后端直接跳过，如实显示"短路跳过"，而非误导的"未接入"。
  const intake = nodeMap.get("INTAKE");
  const securityBlocked =
    (intake?.output as Record<string, unknown> | null)?.["security_action"] === "BLOCK";
  if (securityBlocked && ["intent", "verify", "evidence", "risk"].includes(slot.key)) return "skipped";
  if (slot.key === "human") {
    if (caseStatus === "SUSPENDED") return "current";
    const hr = nodeMap.get("HUMAN_REVIEW");
    if (hr) return hr.status === "FAILED" ? "failed" : "done";
    // 无人工节点：看前置是否完成（已完成/终态单则视为已越过）
    return ["COMPLETED", "APPROVED", "REJECTED"].includes(caseStatus) ? "done" : "pending";
  }
  if (slot.agents.length === 0) return "pending"; // 未接入
  if (runs.length === 0) return "pending";
  if (runs.some((r) => r.status === "FAILED")) return "failed";
  return "done";
}

const STATE_STYLE: Record<SlotState, { dot: string; text: string; tag: string }> = {
  done: { dot: "#28a745", text: "#333", tag: "已完成" },
  current: { dot: "#ffc107", text: "#8a6d00", tag: "进行中" },
  failed: { dot: "#dc3545", text: "#b02a37", tag: "失败" },
  pending: { dot: "#d0d0d0", text: "#aaa", tag: "未接入" },
  skipped: { dot: "#adb5bd", text: "#8a8f98", tag: "短路跳过" },
};

function Timeline({ nodes, status }: { nodes: AgentNode[]; status: string }) {
  const nodeMap = new Map(nodes.map((n) => [n.agent, n]));
  const finalize = nodeMap.get("FINALIZE");
  const slots = finalize
    ? [...SLOTS, { key: "finalize", label: "Finalize 终态", source: "工单1", agents: ["FINALIZE"] }]
    : SLOTS;

  return (
    <div>
      {slots.map((slot, i) => {
        const state = slotState(slot, nodeMap, status);
        const st = STATE_STYLE[state];
        const runs = slot.agents.map((a) => nodeMap.get(a)).filter(Boolean) as AgentNode[];
        const duration = runs.reduce((s, r) => s + (r.duration_ms ?? 0), 0);
        const isLast = i === slots.length - 1;
        return (
          <div key={slot.key} style={{ display: "flex", gap: 12 }}>
            {/* 圆点 + 连接线 */}
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", width: 18, flexShrink: 0 }}>
              <div
                style={{
                  width: 12,
                  height: 12,
                  borderRadius: "50%",
                  background: st.dot,
                  marginTop: 4,
                  animation: state === "current" ? "blink 1s infinite" : undefined,
                  flexShrink: 0,
                }}
              />
              {!isLast && <div style={{ width: 2, flex: 1, background: "#e5e5e5", minHeight: 18 }} />}
            </div>
            {/* 内容 */}
            <div style={{ paddingBottom: isLast ? 0 : 16, minWidth: 0 }}>
              <div style={{ fontSize: 14, fontWeight: 600, color: st.text }}>
                {slot.label}
                <span style={{ fontSize: 11, fontWeight: 400, color: "#999", marginLeft: 8 }}>{slot.source}</span>
              </div>
              <div style={{ fontSize: 12, color: state === "pending" ? "#bbb" : "#777", marginTop: 2 }}>
                {state === "pending" && slot.agents.length === 0
                  ? "未接入（后端模块落地后点亮）"
                  : `${st.tag}${duration > 0 ? ` · ${duration}ms` : ""}`}
                {state === "skipped" && (
                  <span style={{ color: "#8a8f98" }}>（Critic 拦截注入，脏数据不喂给 OCR/LLM）</span>
                )}
                {slot.key === "risk" && runs.length === 2 && (
                  <span style={{ color: "#999" }}>（当前 2 次调用，v4 合并后 1 次）</span>
                )}
                {slot.key === "dlp" && <span style={{ color: "#999" }}>（买家信息已全程打码显示）</span>}
                {runs.some((r) => r.error_tag) && (
                  <span style={{ color: "#b02a37" }}> · {runs.find((r) => r.error_tag)?.error_tag}</span>
                )}
              </div>
            </div>
          </div>
        );
      })}
      <style>{`@keyframes blink { 50% { opacity: 0.35; } }`}</style>
    </div>
  );
}

/* ---------- 页面 ---------- */

export default function CaseDetailPage() {
  const { id } = useParams();
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [graph, setGraph] = useState<CaseGraph | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!id) return;
    try {
      const [d, g] = await Promise.all([
        api.get<CaseDetail>(`/cases/${id}`),
        api.get<CaseGraph>(`/cases/${id}/graph`),
      ]);
      setDetail(d);
      setGraph(g);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [id]);

  useEffect(() => {
    load();
    const token = localStorage.getItem("refund_token") ?? "";
    const es = new EventSource(`/api/v1/cases/${id}/events?token=${encodeURIComponent(token)}`);
    es.onmessage = () => load();
    return () => es.close();
  }, [id, load]);

  if (error) return <p style={{ color: "#dc3545", padding: 24 }}>{error}</p>;
  if (!detail) return <p style={{ padding: 24 }}>加载中...</p>;

  const evidence = detail.evidences[0];
  const nodes = graph?.nodes ?? [];
  const totalMs = nodes.reduce((s, n) => s + (n.duration_ms ?? 0), 0);
  const llmCalls = nodes.filter((n) => ["FRAUD", "SENTIMENT", "DECISION"].includes(n.agent)).length;
  // telemetry：各节点 LLM 真实 token 合计（输入+输出；Fake/规则路径节点为 null 不计）
  const totalTokens = nodes.reduce((s, n) => s + (n.prompt_tokens ?? 0) + (n.completion_tokens ?? 0), 0);
  const fmtTokens = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: 24 }}>
      <Link to="/" style={{ fontSize: 13, color: "#0d6efd" }}>← 返回工作台</Link>
      <h1 style={{ fontSize: 20, margin: "8px 0 16px" }}>
        {detail.ticket_no} <StatusBadge status={detail.status} />
      </h1>

      {/* 三态流转 Stepper（宏观生命周期，与下方 AgentFlow 微观节点时间线互补） */}
      <section style={{ ...card, marginBottom: 16 }}>
        <h2 style={{ fontSize: 14, margin: "0 0 10px", color: "#555" }}>🔀 决策流三态流转</h2>
        <StageStepper status={detail.status} nodes={nodes} />
      </section>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, alignItems: "start" }}>
        {/* 左栏 */}
        <div>
          {/* case 概要（买家脱敏） */}
          <section style={{ ...card, marginBottom: 14 }}>
            <h2 style={{ fontSize: 15, margin: "0 0 10px" }}>📋 case 概要</h2>
            <p style={line}>订单号：{detail.order_id}</p>
            <p style={line}>
              买家：{maskApplicant(detail.applicant_id ?? "")}
              <span style={{ fontSize: 11, color: "#999", marginLeft: 6 }}>（DLP 全程脱敏）</span>
            </p>
            <p style={line}>申请退款：¥{(detail.applicant_amount / 100).toFixed(2)}（实付 ¥{(detail.actual_amount / 100).toFixed(2)}）</p>
            <p style={line}>创建时间：{new Date(detail.created_at).toLocaleString("zh-CN")}</p>
          </section>

          {/* 买家诉求 + 凭证 */}
          <section style={{ ...card, marginBottom: 14 }}>
            <h2 style={{ fontSize: 15, margin: "0 0 10px" }}>💬 买家诉求与凭证</h2>
            <p style={{ fontSize: 13, margin: "4px 0", whiteSpace: "pre-wrap" }}>{detail.description || "（无描述）"}</p>

            {/* 凭证图片预览：image_url 存在就展示原图（可点击放大），区别于"没传图" */}
            <h3 style={{ fontSize: 13, margin: "14px 0 6px" }}>🖼 上传凭证</h3>
            {evidence?.image_url ? (
              <a href={evidence.image_url} target="_blank" rel="noreferrer">
                <img
                  src={evidence.image_url}
                  alt="买家上传的凭证"
                  style={{ maxWidth: "100%", maxHeight: 280, border: "1px solid #e0e0e0", borderRadius: 6 }}
                />
              </a>
            ) : (
              <p style={{ fontSize: 13, color: "#888" }}>未上传图片凭证</p>
            )}

            <h3 style={{ fontSize: 13, margin: "14px 0 6px" }}>🔍 OCR 识别结果</h3>
            {evidence?.ocr_text ? (
              <>
                <pre style={{ background: "#f8f9fa", padding: 10, borderRadius: 6, whiteSpace: "pre-wrap", fontSize: 13, margin: 0 }}>{evidence.ocr_text}</pre>
                <p style={{ fontSize: 12, color: "#666", marginTop: 6 }}>
                  置信度：{evidence.ocr_confidence?.toFixed(3) ?? "-"}
                  {evidence.parse_status === "LOW_CONFIDENCE" && <span style={{ color: "#b02a37" }}>（低置信度预警）</span>}
                </p>
              </>
            ) : evidence?.image_url ? (
              /* 有图但 OCR 没认出文字（NO_TEXT）：OCR 无字不阻断流程，语义判断交给 VL，
                 只展示"未识别到文字"，不再误显示成"无凭证"/"已转人工" */
              <p style={{ fontSize: 13, color: "#888" }}>
                已上传凭证，OCR 未识别到文字（{evidence.parse_status ?? "NO_TEXT"}）——语义判断以下方图片语义理解（VL）为准
              </p>
            ) : (
              <p style={{ fontSize: 13, color: "#888" }}>未上传凭证，OCR 未执行</p>
            )}

            {/* 工单6 扩展：Qwen2.5-VL 图片语义理解（凭证一致性校验输入）
                三种展示：正常语义 -> 蓝色描述块；降级/拦截原因文案（后端以"（"开头落库）
                -> 黄色⚠️提示块，让排查一眼区分"Ollama 挂了"还是"图有问题"；空 -> 兜底 */}
            <h3 style={{ fontSize: 13, margin: "14px 0 6px" }}>🖼 图片语义理解（VL）</h3>
            {evidence?.vision_text ? (
              evidence.vision_text.startsWith("（") ? (
                <p style={{ fontSize: 13, color: "#9a6b00", background: "#fff8e1", padding: 10, borderRadius: 6, margin: 0 }}>
                  ⚠️ {evidence.vision_text}
                </p>
              ) : (
                <>
                  <pre style={{ background: "#f1f8fe", padding: 10, borderRadius: 6, whiteSpace: "pre-wrap", fontSize: 13, margin: 0 }}>{evidence.vision_text}</pre>
                  <p style={{ fontSize: 12, color: "#666", marginTop: 6 }}>
                    视觉模型（Qwen2.5-VL）识别结果，已脱敏；供风控做凭证一致性校验
                  </p>
                </>
              )
            ) : (
              <p style={{ fontSize: 13, color: "#888" }}>未生成视觉描述（无凭证上传时不执行 VL）</p>
            )}
          </section>

          {/* 系统判定理由 */}
          <section style={{ ...card, marginBottom: 14 }}>
            <h2 style={{ fontSize: 15, margin: "0 0 10px" }}>⚖️ 系统判定</h2>
            <p style={line}>决策：{detail.decision ?? "-"}</p>
            <p style={line}>理由：{detail.review_reason ?? "-"}</p>
            <div style={{ display: "flex", gap: 14, marginTop: 8, fontSize: 12, color: "#777" }}>
              <span>欺诈 {detail.fraud_score?.toFixed(2) ?? "-"}</span>
              <span>舆情 {detail.sentiment_score?.toFixed(2) ?? "-"}</span>
              <span>综合 {detail.risk_score?.toFixed(2) ?? "-"}</span>
            </div>
          </section>

          {/* 凭证一致性独立面板（从 fraud_score 拆出，规则优先 + LLM 兜底，可解释可审计） */}
          <section style={{ ...card, marginBottom: 14 }}>
            <h2 style={{ fontSize: 15, margin: "0 0 10px" }}>🧾 凭证一致性</h2>
            {detail.consistency_level ? (
              <>
                <p style={line}>
                  判定：
                  <span
                    style={{
                      padding: "2px 10px",
                      borderRadius: 12,
                      fontSize: 13,
                      color: "#fff",
                      background: CONSISTENCY_META[detail.consistency_level]?.color ?? "#868e96",
                    }}
                  >
                    {CONSISTENCY_META[detail.consistency_level]?.label ?? detail.consistency_level}
                  </span>
                  {!!detail.consistency_penalty && (
                    <span style={{ color: "#dc3545", marginLeft: 8, fontSize: 13 }}>
                      风险信号 +{(detail.consistency_penalty * 100).toFixed(0)} 分（独立展示，不并入风控分）
                    </span>
                  )}
                </p>
                {detail.consistency_dimensions?.length > 0 && (
                  <p style={line}>不符维度：{detail.consistency_dimensions.join("；")}</p>
                )}
                <p style={line}>依据：{detail.consistency_reason ?? "-"}</p>
              </>
            ) : (
              <p style={{ fontSize: 13, color: "#888" }}>未生成一致性判定（无凭证或旧案件）</p>
            )}
          </section>

          {/* 意图识别（双层）：工单8 */}
          <section style={{ ...card, marginBottom: 14 }}>
            <h2 style={{ fontSize: 15, margin: "0 0 10px" }}>🎯 意图识别（双层）</h2>
            {detail.intent ? (
              <>
                <p style={line}>
                  意图：
                  <span
                    style={{
                      padding: "2px 10px",
                      borderRadius: 12,
                      fontSize: 13,
                      color: "#fff",
                      background: INTENT_COLOR[detail.intent] ?? "#868e96",
                    }}
                  >
                    {INTENT_LABEL[detail.intent] ?? detail.intent}
                  </span>
                </p>
                <p style={line}>识别方式：{SOURCE_LABEL[detail.intent_source ?? ""] ?? detail.intent_source ?? "-"}</p>
                <p style={line}>置信度：{(detail.intent_confidence ?? 0).toFixed(2)}</p>
                {detail.intent_fallback && (
                  <p style={{ ...line, color: "#fd7e14" }}>
                    ⚠ 已转人工兜底（换货/不明意图不自动放行）
                  </p>
                )}
              </>
            ) : (
              <p style={{ fontSize: 13, color: "#888" }}>本案件尚未跑意图识别（旧数据或工作流未执行）</p>
            )}
          </section>

          {/* 主管审批操作 */}
          <ReviewPanel detail={detail} onDone={load} />
        </div>

        {/* 右栏：汇总条 + 纵向时间线 */}
        <div>
          <section style={{ ...card, marginBottom: 14, display: "flex", justifyContent: "space-around", padding: "12px 16px" }}>
            <div style={{ textAlign: "center" }}>
              <div style={{ fontSize: 18, fontWeight: 700, color: "#0d6efd" }}>{totalMs > 0 ? `${(totalMs / 1000).toFixed(1)}s` : "-"}</div>
              <div style={{ fontSize: 11, color: "#777" }}>总耗时</div>
            </div>
            <div style={{ textAlign: "center" }}>
              <div style={{ fontSize: 18, fontWeight: 700, color: "#0d6efd" }}>{llmCalls > 0 ? `${llmCalls} 次` : "-"}</div>
              <div style={{ fontSize: 11, color: "#777" }}>LLM 调用（v4 合并后 1 次）</div>
            </div>
            <div style={{ textAlign: "center" }}>
              <div style={{ fontSize: 18, fontWeight: 700, color: totalTokens > 0 ? "#0d6efd" : "#999" }}>
                {fmtTokens(totalTokens)}
              </div>
              <div style={{ fontSize: 11, color: "#777" }}>Token（LLM，telemetry）</div>
            </div>
          </section>

          <section style={card}>
            <h2 style={{ fontSize: 15, margin: "0 0 14px" }}>🔗 决策链路 trace</h2>
            <Timeline nodes={nodes} status={detail.status} />
          </section>
        </div>
      </div>
    </div>
  );
}

const line = { fontSize: 13, margin: "4px 0" } as const;
