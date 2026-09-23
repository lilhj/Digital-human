/** 数字人直播 API：开播 / 停播 / 状态轮询 / 弹幕互动 / 讲解控制。 */
import { api } from "./client";

/** 直播会话状态：idle 未开播 / live 已开播 / failed 失败（creating/preparing 为历史遗留态）。 */
export type LiveState = "idle" | "creating" | "preparing" | "live" | "failed";

export interface LiveStatus {
  state: LiveState;
  running: boolean;
  error: string | null;
  started_at: number | null;
  turns: number;
  /** 后端是否已配置算力云渲染服务地址；false 时按钮应提示先配置 .env */
  configured: boolean;
  /** 最新渲染出的数字人视频（speaking 时播放它） */
  current_video_url: string | null;
  /** 待机循环视频（24s idle 底稿，静音无口型） */
  idle_video_url: string | null;
  /** 数字人是否在"说话"（有已渲染视频在播） */
  speaking: boolean;
  /** 自动讲解：是否开启、当前讲到哪一段、第几轮、累计讲了几段 */
  touting_enabled: boolean;
  segment: string;
  segment_index: number;
  segment_total: number;
  segment_round: number;
  segments_spoken: number;
  /** 是否正被弹幕问答抢占（讲解已挂起） */
  interrupted: boolean;
  /** 当前正在播报的那句话（用于字幕展示） */
  current_line: string;
  product_name: string;
}

export interface LiveStartBody {
  product_id?: number | null;
  persona?: string;
  opening?: boolean;
  touting?: boolean;
}

export interface DanmuResp {
  /** 主播话术（LLM 生成或兜底） */
  reply: string;
  /** 是否已进入渲染队列（渲染完成后数字人开口） */
  spoken: boolean;
  used_llm: boolean;
  /** 是否抢占了正在进行的讲解 */
  interrupted: boolean;
}

export const liveStateLabel: Record<LiveState, string> = {
  idle: "未开播",
  creating: "启动中…",
  preparing: "启动中…",
  live: "直播中",
  failed: "开播失败",
};

export function getLiveStatus(): Promise<LiveStatus> {
  return api.get<LiveStatus>("/live/status");
}

export function startLive(body: LiveStartBody = {}): Promise<LiveStatus> {
  return api.post<LiveStatus>("/live/start", body, false);
}

export function stopLive(): Promise<LiveStatus> {
  return api.post<LiveStatus>("/live/stop", {}, false);
}

export function sendDanmu(question: string): Promise<DanmuResp> {
  return api.post<DanmuResp>("/live/danmu", { question }, false);
}

/** 暂停 / 继续自动讲解（弹幕互动不受影响）。 */
export function setTouting(enabled: boolean): Promise<LiveStatus> {
  return api.post<LiveStatus>("/live/touting", { enabled }, false);
}
