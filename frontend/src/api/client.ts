/** API 客户端：员工侧 JWT 鉴权 + 统一错误处理 + 幂等键生成；买家侧会话（mock，后端落地后换真 token）。 */
import type { EvalReport, IntentSummary, LangfuseTracesResponse, PeriodicReport, RagReport, RedBlueReport, SecuritySummary, TelemetrySummary } from "./types";

const TOKEN_KEY = "refund_token";
const ROLE_KEY = "refund_role";
const NAME_KEY = "refund_display_name";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function getRole(): string | null {
  return localStorage.getItem(ROLE_KEY);
}

export function getDisplayName(): string | null {
  return localStorage.getItem(NAME_KEY);
}

export function setAuth(token: string, role: string, displayName = ""): void {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(ROLE_KEY, role);
  localStorage.setItem(NAME_KEY, displayName);
}

export function clearAuth(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(ROLE_KEY);
  localStorage.removeItem(NAME_KEY);
}

export function genIdempotencyKey(): string {
  return crypto.randomUUID();
}

export class ApiError extends Error {
  code: string;
  status: number;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}, withIdem = false): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (withIdem) headers["X-Idempotency-Key"] = genIdempotencyKey();

  const resp = await fetch(`/api/v1${path}`, { ...options, headers });
  if (resp.status === 401 && path !== "/auth/login") {
    clearAuth();
    window.location.href = "/login";
    throw new ApiError(401, "UNAUTHORIZED", "登录已过期");
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new ApiError(resp.status, data.code ?? "ERROR", data.message ?? "请求失败");
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body: unknown, withIdem = true) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body) }, withIdem),
};

/** 工单8 意图监控大屏聚合数据。 */
export function getIntentSummary(range: string): Promise<IntentSummary> {
  return api.get<IntentSummary>(`/intent/summary?range=${encodeURIComponent(range)}`);
}

/** 评测报告（real=false 离线确定性 / true=LLM-as-a-judge；未运行过返回 404）。 */
export function getEvalReport(real: boolean): Promise<EvalReport> {
  return api.get<EvalReport>(`/eval/report?real=${real}`);
}

/** 运行 Golden Dataset 评测并落盘（MANAGER 限定；real 模式约 1 分钟）。 */
export function runEval(real: boolean): Promise<EvalReport> {
  return api.post<EvalReport>("/eval/run", { real }, false);
}

/** 工单8 周期测试：读取最近一次报告（未跑过 -> ok:false，前端引导先一键触发）。 */
export function getPeriodicReport(): Promise<PeriodicReport> {
  return api.get<PeriodicReport>("/eval/periodic");
}

/** 一键触发周期测试（MANAGER 限定；saveBaseline=true 同时固化为基线）。 */
export function runPeriodicReport(saveBaseline = false): Promise<PeriodicReport> {
  return api.post<PeriodicReport>("/eval/periodic/run", { save_baseline: saveBaseline }, false);
}

/** 买家侧 RAG 客服评测：读取最近一次报告（未跑过 -> ok:false，前端引导先一键触发）。 */
export function getRagReport(): Promise<RagReport> {
  return api.get<RagReport>("/eval/rag");
}

/** 一键触发 RAG 客服评测（MANAGER 限定；real=true 走 LLM 完整链路，需 LLM_API_KEY）。 */
export function runRagReport(real = false): Promise<RagReport> {
  return api.post<RagReport>("/eval/rag/run", { real }, false);
}

/** 安全中心汇总（工单6 三道闸运行期统计 + 事件流水）。 */
export function getSecuritySummary(range: string): Promise<SecuritySummary> {
  return api.get<SecuritySummary>(`/security/summary?range=${encodeURIComponent(range)}`);
}

/** 工单6 红蓝对抗：读取最近一次报告（未跑过 -> ok:false，前端引导先一键触发）。 */
export function getRedBlueReport(): Promise<RedBlueReport> {
  return api.get<RedBlueReport>("/security/red-blue");
}

/** 一键触发红蓝对抗压测（MANAGER 限定；100+ 变种样本，秒级）。 */
export function runRedBlueReport(): Promise<RedBlueReport> {
  return api.post<RedBlueReport>("/security/red-blue/run", {}, false);
}

/** 系统监控聚合（Langfuse 管道 / 节点时延 / DLQ / Token 优化红线）。 */
export function getTelemetrySummary(): Promise<TelemetrySummary> {
  return api.get<TelemetrySummary>("/telemetry/summary");
}

/** Langfuse 云端真实 Trace 直读（public API 代理；未配置/不可达时 ok:false + error）。 */
export function getLangfuseTraces(limit = 10): Promise<LangfuseTracesResponse> {
  return api.get<LangfuseTracesResponse>(`/telemetry/langfuse/traces?limit=${limit}`);
}

/* ---------- 买家侧会话（mock）：后端 customer_auth 落地后替换为真 JWT ---------- */
const BUYER_KEY = "buyer_session";

export interface BuyerSession {
  phone: string;
  nickname: string;
}

export function getBuyer(): BuyerSession | null {
  const raw = localStorage.getItem(BUYER_KEY);
  return raw ? (JSON.parse(raw) as BuyerSession) : null;
}

export function setBuyer(s: BuyerSession): void {
  localStorage.setItem(BUYER_KEY, JSON.stringify(s));
}

export function clearBuyer(): void {
  localStorage.removeItem(BUYER_KEY);
}

/* ---------- 买家侧 RAG 智能客服（POST /api/v1/buyer/chat） ---------- */

export interface BuyerChatResp {
  type: "answer" | "tool" | "refuse";
  // answer：政策问答，知识库原文/LLM 生成 + 引用条目
  answer?: string;
  sources?: string[];
  fallback?: boolean; // true=知识库原文降级（LLM 不可用），false=LLM 生成
  // tool：数据型问题，返回该买家订单真实状态 + 跳转按钮
  message?: string;
  button?: { label: string; href: string };
  orders?: { id: number; order_no: string; status: string; status_label: string; total_cents: number }[];
  // refuse：安全红线 / 超出知识库 -> 拒答转人工
  reason?: "internal" | "execute" | "out_of_kb";
}

/**
 * 发一条买家问题到 RAG 客服。带 buyer_token（已登录）时数据型问题能返回真实订单状态；
 * 未登录时政策问答仍可用，数据型问题返回登录引导（后端 get_optional_customer 自解 token 吞异常）。
 * orderId 可选：数据型问题指定订单，缺省返回最近一笔。
 */
export async function chatBuyer(
  question: string,
  orderId?: number | null,
): Promise<BuyerChatResp> {
  const token = localStorage.getItem("buyer_token");
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const resp = await fetch("/api/v1/buyer/chat", {
    method: "POST",
    headers,
    body: JSON.stringify({ question, order_id: orderId ?? null }),
  });
  const data = (await resp.json().catch(() => ({}))) as BuyerChatResp & { message?: string };
  if (!resp.ok) throw new Error(data.message ?? "客服暂时不可用，请稍后再试");
  return data as BuyerChatResp;
}
