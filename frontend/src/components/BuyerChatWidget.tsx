/** 买家侧 RAG 智能客服悬浮窗（双入口：全局悬浮 + 售后申请页内联）。
 *
 *  - 入口：不传 trigger 时固定右下角悬浮 💬 圆钮；传 trigger 则由调用方决定摆放
 *    （AfterSaleApplyPage 在页面顶部内联一个「联系智能客服」按钮）。
 *  - 三态渲染（对应后端 POST /api/v1/buyer/chat 返回）：
 *    answer  政策问答：知识库原文/LLM 答案 + 引用条目 chip + 降级标识
 *    tool    数据型问题：订单真实状态 + 跳转按钮（未登录给登录引导）
 *    refuse  安全红线 / 超出知识库：拒答转人工，红线用例标红
 *  - 附带 5 个快捷问题（演示便捷 + 覆盖 answer/tool/refuse 三态）。
 */
import { useRef, useState, type CSSProperties, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { chatBuyer, type BuyerChatResp } from "../api/client";
import { DANGER, PRIMARY, WARNING } from "../theme";

interface Msg {
  id: number;
  role: "user" | "assistant";
  text: string;
  resp?: BuyerChatResp;
  loading?: boolean;
}

const SUGGESTIONS: { q: string; hint: string }[] = [
  { q: "我的订单什么时候发货？", hint: "数据型→工具" },
  { q: "手机激活了还能七天无理由退吗？", hint: "政策问答" },
  { q: "七天无理由的七天从哪天开始算？", hint: "政策问答" },
  { q: "支付宝付的，退款多久能到账？", hint: "政策问答" },
  { q: "帮我直接把这笔订单的退款办了", hint: "红线拒答" },
];

const CHIP: CSSProperties = {
  padding: "6px 12px",
  border: "1px solid #d9e5fb",
  background: "#f0f6ff",
  color: "#2a5db0",
  borderRadius: 14,
  fontSize: 12,
  cursor: "pointer",
};

const MSG_BUBBLE: React.CSSProperties = { fontSize: 13, lineHeight: 1.6, whiteSpace: "pre-wrap" };

export default function BuyerChatWidget({
  orderId,
  floating = true,
  trigger,
}: {
  orderId?: number | null;
  floating?: boolean;
  trigger?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([
    {
      id: 1,
      role: "assistant",
      text: "您好，我是米家智能客服 🤖\n可以咨询退货 / 换货 / 运费险 / 保修 / 退款到账等政策，也可以查您的订单状态。",
    },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const idRef = useRef(100); // 固定递增 id，跨渲染不重置（避免 React key 冲突）

  function push(m: Omit<Msg, "id">) {
    const id = ++idRef.current;
    setMsgs((prev) => [...prev, { ...m, id }]);
    return id;
  }

  async function send(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    setInput("");
    push({ role: "user", text: q });
    const loadingId = push({ role: "assistant", text: "…", loading: true });
    setBusy(true);
    try {
      const resp = await chatBuyer(q, orderId);
      setMsgs((prev) =>
        prev.map((m) => (m.id === loadingId ? { ...m, text: "", loading: false, resp } : m)),
      );
    } catch (e) {
      setMsgs((prev) =>
        prev.map((m) =>
          m.id === loadingId
            ? { ...m, loading: false, resp: { type: "refuse", reason: "out_of_kb", message: e instanceof Error ? e.message : "客服暂时不可用" } }
            : m,
        ),
      );
    } finally {
      setBusy(false);
    }
  }

  function renderAssistant(m: Msg) {
    if (m.loading) {
      return <div style={{ ...MSG_BUBBLE, color: "#999" }}>正在思考…</div>;
    }
    const resp = m.resp;
    if (!resp) {
      return <div style={MSG_BUBBLE}>{m.text}</div>;
    }
    if (resp.type === "tool") {
      const isLoginHint = resp.button?.href === "/buyer/login";
      return (
        <div style={{ width: "100%" }}>
          <div style={MSG_BUBBLE}>{resp.message ?? ""}</div>
          {resp.orders && resp.orders.length > 0 && (
            <div style={{ marginTop: 8 }}>
              {resp.orders.map((o) => (
                <div
                  key={o.id}
                  style={{ display: "flex", justifyContent: "space-between", fontSize: 12, padding: "4px 0", borderBottom: "1px dashed #eee" }}
                >
                  <span>#{o.id} {o.order_no}</span>
                  <span style={{ color: "#0d6efd", fontWeight: 600 }}>{o.status_label}</span>
                </div>
              ))}
            </div>
          )}
          {resp.button && (
            <Link
              to={resp.button.href}
              style={{
                ...CHIP,
                display: "inline-block",
                marginTop: 8,
                background: PRIMARY,
                color: "#fff",
                border: "none",
                textDecoration: "none",
              }}
            >
              {resp.button.label}
            </Link>
          )}
          {isLoginHint && (
            <div style={{ fontSize: 11, color: "#888", marginTop: 6 }}>
              登录后可查看该订单的实时发货状态。
            </div>
          )}
        </div>
      );
    }
    if (resp.type === "refuse") {
      const isRedLine = resp.reason === "internal" || resp.reason === "execute";
      return (
        <div
          style={{
            width: "100%",
            borderLeft: `3px solid ${isRedLine ? DANGER : WARNING}`,
            paddingLeft: 10,
          }}
        >
          <div style={MSG_BUBBLE}>{resp.message ?? ""}</div>
          {isRedLine && (
            <div style={{ fontSize: 11, color: DANGER, marginTop: 6 }}>
              ⚠ 安全红线：客服不处理退款执行 / 内部决策信息
            </div>
          )}
        </div>
      );
    }
    // answer：政策问答
    return (
      <div style={{ width: "100%" }}>
        <div style={MSG_BUBBLE}>{resp.answer ?? m.text}</div>
        {resp.sources && resp.sources.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
            {resp.sources.map((s) => (
              <span key={s} style={{ ...CHIP, padding: "2px 8px", background: "#fff", color: "#888" }}>
                依据 {s}
              </span>
            ))}
          </div>
        )}
        {resp.fallback === true && (
          <div style={{ fontSize: 11, color: "#999", marginTop: 6 }}>来源：知识库原文</div>
        )}
      </div>
    );
  }

  const drawer = (
    <div
      style={{
        position: "fixed",
        right: 24,
        bottom: floating ? 84 : 96,
        width: 360,
        maxHeight: "70vh",
        background: "#fff",
        border: "1px solid #eee",
        borderRadius: 12,
        boxShadow: "0 8px 30px rgba(0,0,0,.15)",
        display: "flex",
        flexDirection: "column",
        zIndex: 200,
      }}
    >
      {/* 头部 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "12px 16px",
          borderBottom: "1px solid #eee",
        }}
      >
        <span style={{ fontSize: 15, fontWeight: 700 }}>💬 智能客服</span>
        <button onClick={() => setOpen(false)} style={{ border: "none", background: "none", cursor: "pointer", fontSize: 16, color: "#888" }}>
          ✕
        </button>
      </div>

      {/* 消息区 */}
      <div style={{ flex: 1, overflowY: "auto", padding: 14, display: "flex", flexDirection: "column", gap: 12, minHeight: 200 }}>
        {msgs.map((m) =>
          m.role === "user" ? (
            <div key={m.id} style={{ display: "flex", justifyContent: "flex-end" }}>
              <div style={{ ...MSG_BUBBLE, background: "#e7f1ff", borderRadius: "10px 10px 2px 10px", padding: "8px 12px", maxWidth: "82%" }}>
                {m.text}
              </div>
            </div>
          ) : (
            <div key={m.id} style={{ display: "flex" }}>
              <div style={{ background: "#f7f8fa", borderRadius: "10px 10px 10px 2px", padding: "8px 12px", maxWidth: "86%" }}>
                {renderAssistant(m)}
              </div>
            </div>
          ),
        )}
      </div>

      {/* 快捷问题 */}
      <div style={{ padding: "0 14px 8px", display: "flex", flexWrap: "wrap", gap: 6 }}>
        {SUGGESTIONS.map((s) => (
          <button key={s.q} onClick={() => send(s.q)} style={CHIP} title={s.hint}>
            {s.q}
          </button>
        ))}
      </div>

      {/* 输入区 */}
      <div style={{ display: "flex", gap: 8, padding: 12, borderTop: "1px solid #eee" }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send(input)}
          placeholder="输入问题，如：运费险能赔多少？"
          style={{ flex: 1, padding: "8px 12px", borderRadius: 6, border: "1px solid #ccc", fontSize: 13, boxSizing: "border-box" }}
        />
        <button
          onClick={() => send(input)}
          disabled={busy}
          style={{ padding: "8px 16px", background: PRIMARY, color: "#fff", border: "none", borderRadius: 6, cursor: "pointer", fontSize: 13 }}
        >
          发送
        </button>
      </div>
    </div>
  );

  return (
    <>
      {trigger ? (
        <span onClick={() => setOpen(true)} style={{ cursor: "pointer", display: "inline-block" }}>
          {trigger}
        </span>
      ) : floating ? (
        <button
          onClick={() => setOpen(!open)}
          title="智能客服"
          style={{
            position: "fixed",
            right: 24,
            bottom: 24,
            width: 52,
            height: 52,
            borderRadius: "50%",
            background: PRIMARY,
            color: "#fff",
            border: "none",
            fontSize: 22,
            cursor: "pointer",
            boxShadow: "0 4px 14px rgba(13,110,253,.4)",
            zIndex: 200,
          }}
        >
          💬
        </button>
      ) : null}
      {open && drawer}
    </>
  );
}
