/** 与后端接口对应的类型定义。 */

export interface LoginResponse {
  access_token: string;
  token_type: string;
  role: string;
  display_name: string;
}

export interface CaseSummary {
  id: number;
  ticket_no: string;
  order_id: string;
  applicant_amount: number;
  status: string;
  risk_score: number | null;
  sentiment_score: number | null;
  description: string;
  decision: string | null;
  review_reason: string | null;
  // 工单8 意图识别（列表/详情展示）
  intent: string | null;
  intent_source: string | null;
  intent_confidence: number | null;
  intent_fallback: boolean | null;
  created_at: string;
}

export interface CaseListResponse {
  total: number;
  items: CaseSummary[];
}

export interface Evidence {
  image_url: string | null;
  ocr_text: string | null;
  ocr_confidence: number | null;
  parse_status: string | null;
}

export interface CaseDetail {
  id: number;
  ticket_no: string;
  order_id: string;
  applicant_id: string;
  applicant_amount: number;
  actual_amount: number;
  status: string;
  fraud_score: number | null;
  sentiment_score: number | null;
  risk_score: number | null;
  decision: string | null;
  review_reason: string | null;
  // 工单8 意图识别
  intent: string | null;
  intent_source: string | null;
  intent_confidence: number | null;
  intent_fallback: boolean | null;
  description: string;
  created_at: string;
  updated_at: string;
  evidences: Evidence[];
}

/** 意图监控大屏聚合（GET /api/v1/intent/summary）。 */
export interface IntentDistribution {
  intent: string;
  label: string;
  count: number;
  color: string;
}

export interface IntentRoute {
  route: string;
  count: number;
  note: string;
}

export interface IntentDetailRow {
  time: string;
  text: string;
  intent: string;
  method: string;
  conf: number;
  route: string;
}

export interface IntentSummary {
  range: string;
  total_identified: number;
  distribution: IntentDistribution[];
  to_human: number;
  source_breakdown: Record<string, number>;
  routes: IntentRoute[];
  details: IntentDetailRow[];
  eval: {
    recall: number;
    hallucination_rate: number;
    token_reduction: number;
    rule_hit_rate: number;
    samples: number;
  };
}

export interface AgentNode {
  agent: string;
  status: string;
  error_tag: string | null;
  duration_ms: number | null;
  output: Record<string, unknown> | null;
  finished_at: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
}

export interface CaseGraph {
  case_id: number;
  status: string;
  nodes: AgentNode[];
}

export interface Overview {
  total: number;
  suspended: number;
  today_created: number;
  today_completed: number;
  risk_distribution: { low: number; medium: number; high: number };
  status_counts: Record<string, number>;
}

export interface DecisionResponse {
  case_id: number;
  status: string;
  message: string;
}

export interface EvalScore {
  correctness: number;
  safety: number;
  efficiency: number;
  reason: string;
  confidence: number;
}

export interface EvalCase {
  id: string;
  scenario: string;
  kind: string; // decision / security / quality
  expected: string;
  actual: string;
  match: boolean;
  reason: string;
  score: EvalScore;
}

export interface EvalAggregate {
  correctness: number;
  safety: number;
  efficiency: number;
  total: number;
  count: number;
}

/** GET /eval/report 与 POST /eval/run 的返回结构。 */
export interface EvalReport {
  mode: string; // mock（离线确定性）/ real（LLM-as-a-judge）
  aggregate: EvalAggregate;
  cases: EvalCase[];
  generated_at?: string;
}

/** 周期测试红线判定。 */
export interface PeriodicRedLines {
  all_pass: boolean;
  fails: string[];
  recall_pass: boolean;
  hallucination_pass: boolean;
  token_reduction_pass: boolean;
}

/** 工单8 周期测试报告（GET /eval/periodic 与 POST /eval/periodic/run）。 */
export interface PeriodicReport {
  ok: boolean;
  has_report?: boolean;
  error?: string;
  generated_at?: string | null;
  samples?: number;
  rule_hit_rate?: number;
  Recall?: number;
  HallucinationRate?: number;
  TokenReduction?: number;
  TTFTReduction?: number;
  has_baseline?: boolean;
  regression_notes?: string[];
  red_lines?: PeriodicRedLines;
}

/** RAG 客服评测红线判定（5 维）。 */
export interface RagRedLines {
  all_pass: boolean;
  fails: string[];
  recall_pass: boolean;
  answer_pass: boolean;
  refuse_pass: boolean;
  route_pass: boolean;
  hallucination_pass: boolean;
}

/** RAG 客服评测聚合指标。 */
export interface RagAggregate {
  policy_total: number;
  refuse_total: number;
  route_total: number;
  recall_n: number;
  answer_n: number;
  halluc_n: number;
  refuse_n: number;
  route_n: number;
  Recall: number;
  AnswerRate: number;
  HallucinationRate: number;
  RefuseRate: number;
  RouteRate: number;
}

/** RAG 客服评测单用例结果（kind: policy / refuse / route）。 */
export interface RagEvalCase {
  id: string;
  scenario: string;
  kind: "policy" | "refuse" | "route";
  question: string;
  resp_type: string;
  note?: string;
  pass: boolean;
  expected?: string[];
  top3?: string[];
  recall_pass?: boolean;
  answer_pass?: boolean;
  hallucinations?: string[];
  missing_keywords?: string[];
  covered?: string[];
  reason?: string;
  expected_reason?: string;
  route_pass?: boolean;
  button?: string;
}

/** 买家侧 RAG 客服评测报告（GET /eval/rag 与 POST /eval/rag/run）。 */
export interface RagReport {
  ok: boolean;
  has_report?: boolean;
  error?: string;
  generated_at?: string | null;
  mode?: string; // mock（确定性原文路径）/ real（LLM 完整链路）
  aggregate?: RagAggregate;
  red_lines?: RagRedLines;
  cases?: RagEvalCase[];
}

export interface SecurityEvent {
  time: string;
  type: string;
  case_id: number;
  ticket_no: string;
  detail: string;
  level: string; // 高 / 中 / 低
}

/** GET /security/summary：工单6 三道闸运行期统计。 */
export interface SecuritySummary {
  range: string;
  critic: { scanned: number; blocked: number; block_rate: number; injection: number; jailbreak: number };
  dlp: { scanned: number; pii_hits: number; hit_rate: number; masked_chars_total: number };
  tool_filter: { blocked: number };
  sandbox: { mode: string }; // off / on
  events: SecurityEvent[];
}

/** 工单6 红蓝对抗报告（GET /security/red-blue 与 POST /security/red-blue/run）。 */
export interface RedBlueReport {
  ok: boolean;
  has_report?: boolean;
  error?: string;
  generated_at?: string | null;
  elapsed_s?: number;
  total_attacks?: number;
  blocked?: number;
  intercept_rate?: number;
  inj_rate?: number;
  inj_blocked?: number;
  inj_total?: number;
  jb_rate?: number;
  jb_blocked?: number;
  jb_total?: number;
  evasive_total?: number;
  evasive_blocked?: number;
  evasive_missed_examples?: { score: number; text: string }[];
  dlp?: {
    pii_total: number;
    miss: number;
    miss_rate: number;
    normal_total: number;
    fp: number;
    fp_rate: number;
    miss_examples: { text: string; leaked: string[] }[];
    fp_examples: string[];
  };
  red_lines?: { all_pass: boolean; fails: string[] };
}

export interface SpoolEntry {
  time: string;
  node: string;
  case_id: number;
  duration_ms: number;
  status: string; // pending / failed
  attempts: number;
}

export interface NodeLatency {
  node: string;
  label: string;
  count: number;
  avg_ms: number;
  p95_ms: number;
}

export interface DlqItem {
  time: string;
  stage: string;
  case_id: number | null;
  error: string;
}

/** GET /telemetry/summary：系统监控聚合（上报管道 / 时延 / DLQ / 优化红线）。 */
export interface TelemetrySummary {
  langfuse: { enabled: boolean; keys_configured: boolean; module_installed: boolean; base_url: string };
  spool: { total: number; pending: number; failed: number; recent: SpoolEntry[] };
  node_latency: NodeLatency[];
  end_to_end: { count: number; avg_ms: number; p95_ms: number };
  dlq: { length: number; items: DlqItem[] };
  optimization: { token_reduction: number; rule_hit_rate: number };
}

/* -------- Langfuse 云端直读（GET /telemetry/langfuse/traces） -------- */

export interface LangfuseObservation {
  id: string;
  type: string; // GENERATION / AGENT / SPAN / EVENT
  name: string;
  latency_ms?: number | null;
}

export interface LangfuseTrace {
  id: string;
  name: string;
  timestamp?: string;
  case_id?: number | null;
  node?: string;
  duration_ms?: number | null;
  status?: string;
  obs_count: number;
  observations: LangfuseObservation[];
}

export interface LangfuseTracesResponse {
  ok: boolean;
  error?: string | null;
  total_traces?: number | null;
  total_observations?: number | null;
  traces: LangfuseTrace[];
}

/* ---------- ADMIN 用户管理（phase12） ---------- */

export interface AdminUser {
  id: number;
  username: string;
  role: string; // CSR / MANAGER / ADMIN
  display_name: string;
  is_active: boolean;
  created_at: string;
}

export interface AdminCustomer {
  id: number;
  phone: string;
  nickname: string | null;
  is_active: boolean;
  created_at: string;
}

export interface AdminActionResp {
  ok: boolean;
  message: string;
}
