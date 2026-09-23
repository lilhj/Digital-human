/** 数字人直播（算力云自部署数字人版）。
 *
 *  链路：算力云渲染服务（CosyVoice2 TTS + MuseTalk 口型）→ mp4 → 前端 <video> 播放；
 *        自动讲解：后端 7 段直播 SOP + LLM 轮换，按渲染视频时长推进节奏；
 *        观众弹幕 → LLM 生成话术 → 排队渲染（讲解自动让位，答完续讲）。
 *  对话大脑不变：agicto · deepseek-v4-flash；渲染一句话术约 1~2 分钟。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  getLiveStatus,
  liveStateLabel,
  sendDanmu,
  setTouting,
  startLive,
  stopLive,
  type LiveStatus,
} from "../../api/live";
import { btnGhost, btnPrimary, card, fen } from "../../theme";

/** 当前讲解中的商品（Phase 1.5 改为后端 Agent 决定推哪个商品 + 真实库存）。 */
const DEMO_PRODUCT = {
  id: 24648,
  name: "Xiaomi 17 Max",
  desc: "第五代骁龙8至尊版 | 8000mAh 小米金沙江电池 | 6.9英寸新一代超级阳光屏",
  price: 479900, // 单位：分
  stock: 128,
  image: "/products/images/24648.png", // 静态资源真实路径：public/products/images/
};

/** 弹幕条目：用户提问 or 主播回应。 */
interface Danmu {
  user: string;
  text: string;
  isAnchor?: boolean;
  pending?: boolean;
}

const WELCOME_DANMU: Danmu[] = [
  { user: "小米粒", text: "主播，这个和 Xiaomi 17 有什么区别？" },
  { user: "数码控", text: "8000mAh 电池有点狠" },
  { user: "路人甲", text: "现在下单什么时候能发货？" },
];

const POLL_MS = 3000;

export default function LiveSellPage() {
  const [status, setStatus] = useState<LiveStatus | null>(null);
  const [danmu, setDanmu] = useState<Danmu[]>(WELCOME_DANMU);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [muted, setMuted] = useState(true); // 浏览器自动播放策略：默认静音，手动开启
  // 当前正在播放的渲染视频；播完（onEnded）清空回退待机循环，避免冻在最后一帧
  const [playedUrl, setPlayedUrl] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);

  const refresh = useCallback(async () => {
    try {
      setStatus(await getLiveStatus());
    } catch {
      /* 轮询失败静默：后端未启动时不该刷一片红 */
    }
  }, []);

  // 开播后每 3 秒同步一次状态（含建流/模型加载的中间态）
  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    listRef.current?.scrollTo({ top: 999999 });
  }, [danmu]);

  const onStart = async () => {
    setBusy(true);
    setNotice(null);
    try {
      const next = await startLive({ product_id: DEMO_PRODUCT.id, opening: true });
      setStatus(next);
      setNotice("已开播。数字人形象就位，第一段话术正在算力云渲染（约 1~2 分钟后开口）");
    } catch (e) {
      setNotice((e as Error).message);
      void refresh();
    } finally {
      setBusy(false);
    }
  };

  const onStop = async () => {
    setBusy(true);
    try {
      setStatus(await stopLive());
      setNotice("已停播");
    } catch (e) {
      setNotice((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onSend = async () => {
    const question = draft.trim();
    if (!question || busy) return;
    setDraft("");
    const userIdx = danmu.length;
    setDanmu((prev) => [...prev, { user: "我", text: question }]);
    try {
      const resp = await sendDanmu(question);
      setDanmu((prev) => [
        ...prev.slice(0, userIdx + 1),
        { user: "主播", text: resp.reply, isAnchor: true },
      ]);
      if (!resp.spoken) setNotice("话术已生成，但未能进入渲染队列（渲染服务异常）");
    } catch (e) {
      setNotice((e as Error).message);
    }
  };

  const live = status?.state === "live";
  const starting = status?.state === "creating" || status?.state === "preparing";
  const stateLabel = status ? liveStateLabel[status.state] : "加载中";
  const stateColor = live ? "#28a745" : starting ? "#ffc107" : status?.state === "failed" ? "#dc3545" : "#999";
  const touting = status?.touting_enabled ?? false;
  // 数字人画面：有新渲染视频 → 播它一遍；播完/空闲 → 待机循环（不冻帧）
  useEffect(() => {
    const url = status?.current_video_url;
    if (status?.speaking && url && url !== playedUrl) setPlayedUrl(url);
  }, [status, playedUrl]);
  const speaking = live && !!playedUrl;
  const showUrl = live ? (playedUrl ?? status?.idle_video_url ?? null) : null;

  const onToggleTouting = async () => {
    if (!status) return;
    setBusy(true);
    try {
      setStatus(await setTouting(!status.touting_enabled));
    } catch (e) {
      setNotice((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: "flex", gap: 20, alignItems: "flex-start" }}>
      {/* ===================== 左侧：直播画面 + 弹幕 ===================== */}
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 14 }}>
        {/* 直播画面（16:9 舞台；竖屏数智人流由播放器 contain 居中显示） */}
        <div
          style={{
            position: "relative",
            width: "100%",
            paddingTop: "56.25%", // 16:9
            background: "#111",
            borderRadius: 8,
            overflow: "hidden",
          }}
        >
          {live && showUrl ? (
            <video
              key={showUrl}
              src={showUrl}
              autoPlay
              loop={!speaking}
              muted={muted}
              playsInline
              onEnded={() => setPlayedUrl(null)}
              style={{
                position: "absolute",
                inset: 0,
                width: "100%",
                height: "100%",
                objectFit: "contain",
                background: "#000",
              }}
            />
          ) : (
            <div
              style={{
                position: "absolute",
                inset: 0,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                gap: 10,
                padding: 20,
                textAlign: "center",
              }}
            >
              <div style={{ color: "#bbb", fontSize: 14 }}>
                {starting ? stateLabel : "数字人未开播"}
              </div>
              <div style={{ color: "#666", fontSize: 12, lineHeight: 1.6 }}>
                {status?.configured === false
                  ? "尚未配置渲染服务：请在 .env 填 DH_API_BASE_URL（算力云渲染服务地址）"
                  : "点击右侧「开始直播」，算力云渲染完成后此处播放数字人视频"}
              </div>
            </div>
          )}
          {/* 声音开关：浏览器自动播放策略要求默认静音 */}
          {live && (
            <button
              onClick={() => setMuted((m) => !m)}
              style={{
                position: "absolute",
                left: 10,
                bottom: 10,
                padding: "4px 10px",
                background: "rgba(0, 0, 0, 0.55)",
                color: "#fff",
                fontSize: 12,
                border: "none",
                borderRadius: 4,
                cursor: "pointer",
              }}
            >
              {muted ? "开启声音" : "静音"}
            </button>
          )}
          {/* 合规标识：常驻右上角，Phase 3 也不可移除 */}
          <div
            style={{
              position: "absolute",
              top: 10,
              right: 10,
              padding: "3px 8px",
              background: "rgba(0, 0, 0, 0.55)",
              color: "#fff",
              fontSize: 11,
              borderRadius: 4,
            }}
          >
            AI 数字人主播
          </div>
          {/* 字幕条：显示正在播报的那句话，模拟真直播字幕（左侧让开「开启声音」按钮） */}
          {live && status?.current_line && (
            <div
              style={{
                position: "absolute",
                left: 110,
                right: 12,
                bottom: 12,
                padding: "8px 12px",
                background: "rgba(0, 0, 0, 0.62)",
                color: "#fff",
                fontSize: 13,
                lineHeight: 1.5,
                borderRadius: 6,
              }}
            >
              {status.current_line}
            </div>
          )}
        </div>

        {/* 弹幕区（Phase 2 已打通：提问 → LLM 话术 → 数字人播报） */}
        <div style={{ ...card, display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: "#333" }}>直播间互动</div>
            <div style={{ fontSize: 12, color: stateColor }}>{stateLabel}</div>
          </div>
          <div
            ref={listRef}
            style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 150, overflowY: "auto" }}
          >
            {danmu.map((d, i) => (
              <div key={i} style={{ fontSize: 13, color: d.isAnchor ? "#0d6efd" : "#555" }}>
                <span style={{ color: d.isAnchor ? "#dc3545" : "#0d6efd" }}>{d.user}</span>
                <span style={{ margin: "0 6px", color: "#ccc" }}>·</span>
                {d.text}
              </div>
            ))}
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void onSend();
              }}
              disabled={!live}
              placeholder={live ? "输入弹幕提问，主播会实时回答" : "开播后可发送弹幕互动"}
              style={{
                flex: 1,
                padding: 8,
                borderRadius: 6,
                border: "1px solid #e0e0e0",
                background: live ? "#fff" : "#fafafa",
                fontSize: 13,
                color: live ? "#333" : "#999",
              }}
            />
            <button
              onClick={() => void onSend()}
              disabled={!live}
              style={{ ...btnGhost, opacity: live ? 1 : 0.5, cursor: live ? "pointer" : "not-allowed" }}
            >
              发送
            </button>
          </div>
        </div>
      </div>

      {/* ===================== 右侧：讲解中商品 + 直播状态 ===================== */}
      <div style={{ width: 300, flexShrink: 0, display: "flex", flexDirection: "column", gap: 14 }}>
        <div style={{ ...card, padding: 0, overflow: "hidden" }}>
          <div
            style={{
              height: 180,
              background: "#fff",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              borderBottom: "1px solid #f0f0f0",
            }}
          >
            <img
              src={DEMO_PRODUCT.image}
              alt={DEMO_PRODUCT.name}
              style={{ width: "100%", height: "100%", objectFit: "contain" }}
            />
          </div>
          <div style={{ padding: 14 }}>
            <div style={{ fontSize: 15, fontWeight: 600 }}>{DEMO_PRODUCT.name}</div>
            <div style={{ fontSize: 12, color: "#999", marginTop: 4, lineHeight: 1.6 }}>
              {DEMO_PRODUCT.desc}
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 10 }}>
              <span style={{ color: "#dc3545", fontWeight: 700, fontSize: 18 }}>
                {fen(DEMO_PRODUCT.price)}
              </span>
              <span style={{ fontSize: 12, color: "#999" }}>库存 {DEMO_PRODUCT.stock}</span>
            </div>
          </div>
        </div>

        <div style={{ ...card, display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: "#333" }}>直播状态</div>
          <div style={{ fontSize: 12, color: "#888", lineHeight: 1.9 }}>
            状态：<span style={{ color: stateColor }}>{stateLabel}</span>
            <br />
            对话大脑：agicto · deepseek-v4-flash
            <br />
            渲染链路：算力云 · CosyVoice2 + MuseTalk
            {live && (
              <>
                <br />
                已互动：{status?.turns ?? 0} 轮
              </>
            )}
          </div>

          {/* 自动讲解进度：讲解段落由后端 7 段 SOP 循环推进，弹幕会临时抢占 */}
          {live && (
            <div
              style={{
                fontSize: 12,
                color: "#888",
                lineHeight: 1.9,
                borderTop: "1px solid #f0f0f0",
                paddingTop: 8,
              }}
            >
              自动讲解：
              <span style={{ color: touting ? "#28a745" : "#999" }}>
                {touting ? "进行中" : "已暂停"}
              </span>
              {status?.interrupted && <span style={{ color: "#dc3545" }}>（弹幕插话中）</span>}
              <br />
              当前环节：{status?.segment || "准备中"}
              {!!status && status.segment_total > 0 && (
                <>（{status.segment_index + 1}/{status.segment_total}）</>
              )}
              <br />
              第 {status?.segment_round ?? 1} 轮 · 已讲 {status?.segments_spoken ?? 0} 段
            </div>
          )}

          {(notice || status?.error) && (
            <div style={{ fontSize: 12, color: "#dc3545", lineHeight: 1.6 }}>
              {status?.error ?? notice}
            </div>
          )}

          {live ? (
            <button onClick={() => void onStop()} disabled={busy} style={{ ...btnGhost }}>
              结束直播
            </button>
          ) : (
            <button
              onClick={() => void onStart()}
              disabled={busy || starting}
              style={{ ...btnPrimary, opacity: busy || starting ? 0.5 : 1, cursor: busy || starting ? "not-allowed" : "pointer" }}
            >
              {starting ? stateLabel : "开始直播"}
            </button>
          )}
          <button
            onClick={() => void onToggleTouting()}
            disabled={busy || !live}
            style={{ ...btnGhost, opacity: live ? 1 : 0.5, cursor: live ? "pointer" : "not-allowed" }}
          >
            {touting ? "暂停讲解" : "继续讲解"}
          </button>
          <button disabled style={{ ...btnPrimary, opacity: 0.5, cursor: "not-allowed" }}>
            加入购物车
          </button>
        </div>
      </div>
    </div>
  );
}
