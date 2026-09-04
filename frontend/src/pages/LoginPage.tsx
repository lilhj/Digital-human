/** 员工登录页：左右分屏（左品牌叙事 / 右表单）+ JWT 存储。
 *  买家请走 /buyer。样式全内联、不引 UI 库，与其他页面保持一致。 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, setAuth } from "../api/client";
import type { LoginResponse } from "../api/types";

/* ---------- 演示账号（点击一键填充） ---------- */
const DEMOS = [
  { u: "manager", p: "manager123", label: "主管 · 张伟" },
  { u: "csr", p: "csr123", label: "客服 · 李娜" },
  { u: "admin", p: "admin123", label: "管理员 · 王强" },
];

/* ---------- 左面板 KPI / 实时流（展示用固定值） ---------- */
const KPIS = [
  { v: "128,640", k: "累计决策工单" },
  { v: "92.3%", k: "意图召回率" },
  { v: "1.4%", k: "幻觉率" },
  { v: "1.8s", k: "平均决策时延" },
];
const FEED = [
  { tag: "退款", text: "手机壳破损，OCR 凭证已核验", t: "1.2s", amt: "¥29.00" },
  { tag: "换货", text: "舆情中风险，已转人工复核", t: "挂起", amt: "¥1,299" },
  { tag: "退货", text: "金额超护栏阈值，拦截待批", t: "主管", amt: "¥4,999" },
];

export default function LoginPage() {
  const [username, setUsername] = useState("manager");
  const [password, setPassword] = useState("manager123");
  const [activeDemo, setActiveDemo] = useState("manager");
  const [showPwd, setShowPwd] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [narrow, setNarrow] = useState(window.innerWidth < 900);
  const navigate = useNavigate();

  // 窄屏（<900px）塌成上下单栏，内联样式无法写 media query，用监听实现
  useEffect(() => {
    const onResize = () => setNarrow(window.innerWidth < 900);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const data = await api.post<LoginResponse>("/auth/login", { username, password }, false);
      setAuth(data.access_token, data.role, data.display_name);
      navigate("/");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  function fillDemo(d: (typeof DEMOS)[number]) {
    setUsername(d.u);
    setPassword(d.p);
    setActiveDemo(d.u);
    setError("");
  }

  return (
    <div style={{
      minHeight: "100vh", background: "#eef1f6", display: "flex",
      alignItems: "center", justifyContent: "center", padding: narrow ? 16 : 32,
    }}>
      <div style={{
        width: "100%", maxWidth: 1080, background: "#fff",
        border: "1px solid #e6e9f0", borderRadius: 20, overflow: "hidden",
        display: "grid", gridTemplateColumns: narrow ? "1fr" : "1.15fr 1fr",
        boxShadow: "0 24px 60px rgba(16,24,40,.14)",
      }}>

        {/* ================= 左：品牌叙事区 ================= */}
        <aside style={{
          position: "relative", overflow: "hidden", padding: narrow ? 32 : 44,
          color: "#fff", display: "flex", flexDirection: "column",
          background: "radial-gradient(1100px 520px at 8% -10%, rgba(255,255,255,.20), transparent 60%), linear-gradient(150deg, #0b3d91 0%, #0d6efd 55%, #12b4c8 100%)",
        }}>
          {/* 抽象网格（工单流线） */}
          <div style={{
            position: "absolute", inset: 0, pointerEvents: "none", opacity: 0.5,
            backgroundImage: "linear-gradient(rgba(255,255,255,.16) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.16) 1px, transparent 1px)",
            backgroundSize: "46px 46px",
            maskImage: "radial-gradient(720px 420px at 88% 8%, #000 0%, transparent 78%)",
            WebkitMaskImage: "radial-gradient(720px 420px at 88% 8%, #000 0%, transparent 78%)",
          }} />
          <div style={{ position: "relative" }}>
            {/* Logo */}
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <div style={{
                width: 40, height: 40, borderRadius: 12, flex: "none", fontWeight: 800, fontSize: 18,
                background: "rgba(255,255,255,.16)", border: "1px solid rgba(255,255,255,.30)",
                display: "flex", alignItems: "center", justifyContent: "center",
              }}>退</div>
              <div>
                <b style={{ display: "block", fontSize: 17, letterSpacing: 0.6 }}>客诉舆情退赔决策系统</b>
                <span style={{ display: "block", fontSize: 11, color: "rgba(255,255,255,.72)", letterSpacing: 1.6, textTransform: "uppercase" }}>
                  Refund &amp; Sentiment Decision Platform
                </span>
              </div>
            </div>

            {/* Hero */}
            <div style={{ marginTop: narrow ? 24 : 46 }}>
              <h1 style={{ margin: "0 0 10px", fontSize: narrow ? 24 : 30, lineHeight: 1.32, fontWeight: 700 }}>
                让每一笔退赔决策<br />都有据可依
              </h1>
              <p style={{ margin: 0, fontSize: 14, lineHeight: 1.85, color: "rgba(255,255,255,.72)", maxWidth: 380 }}>
                多 Agent 协同调度 · 意图双通道识别 · 全程敏感信息脱敏 · 人工断点干预，把退款风险挡在资损之前。
              </p>
            </div>

            {/* 能力 chip */}
            <div style={{ marginTop: 22, display: "flex", flexWrap: "wrap", gap: 8 }}>
              {["8 节点决策流水线", "金额边界护栏", "舆情风险合并判定", "LLM-as-Judge 评测"].map((c) => (
                <span key={c} style={{
                  fontSize: 12, padding: "5px 11px", borderRadius: 999, color: "#fff",
                  background: "rgba(255,255,255,.14)", border: "1px solid rgba(255,255,255,.26)",
                }}>{c}</span>
              ))}
            </div>

            {/* KPI */}
            <div style={{ marginTop: narrow ? 28 : 42, display: "grid", gridTemplateColumns: narrow ? "repeat(2,1fr)" : "repeat(4,1fr)", gap: 10 }}>
              {KPIS.map((k) => (
                <div key={k.k} style={{
                  padding: "12px 12px 11px", borderRadius: 12,
                  background: "rgba(255,255,255,.12)", border: "1px solid rgba(255,255,255,.20)",
                }}>
                  <b style={{ display: "block", fontSize: 20 }}>{k.v}</b>
                  <span style={{ display: "block", marginTop: 3, fontSize: 11, color: "rgba(255,255,255,.72)" }}>{k.k}</span>
                </div>
              ))}
            </div>

            {/* 实时决策流 */}
            {!narrow && (
              <div style={{ marginTop: 36 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "rgba(255,255,255,.72)", marginBottom: 10 }}>
                  <span style={{ width: 7, height: 7, borderRadius: "50%", background: "#31d158", boxShadow: "0 0 0 4px rgba(49,209,88,.22)" }} />
                  实时决策流 · 演示数据
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  {FEED.map((f) => (
                    <div key={f.text} style={{
                      display: "flex", alignItems: "center", gap: 10, fontSize: 12.5, padding: "9px 12px",
                      borderRadius: 10, background: "rgba(255,255,255,.10)", border: "1px solid rgba(255,255,255,.16)",
                    }}>
                      <span style={{ fontSize: 10.5, padding: "2px 7px", borderRadius: 6, background: "rgba(255,255,255,.20)", flex: "none" }}>{f.tag}</span>
                      <span>{f.text}</span>
                      <span style={{ color: "rgba(255,255,255,.72)", fontSize: 11 }}>· {f.t}</span>
                      <b style={{ marginLeft: "auto" }}>{f.amt}</b>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* 信任条 */}
            <div style={{
              marginTop: 22, paddingTop: 16, borderTop: "1px solid rgba(255,255,255,.16)",
              display: "flex", gap: 18, fontSize: 11.5, color: "rgba(255,255,255,.72)",
            }}>
              <span>🔒 数据全程脱敏</span>
              <span>🛡️ 防资损护栏</span>
              <span>📊 决策可追溯</span>
            </div>
          </div>
        </aside>

        {/* ================= 右：表单区 ================= */}
        <main style={{ padding: narrow ? "34px 28px" : "52px 48px", display: "flex", flexDirection: "column" }}>
          <h2 style={{ margin: "0 0 6px", fontSize: 22, letterSpacing: 0.4 }}>登录运营控制台</h2>
          <p style={{ margin: "0 0 26px", fontSize: 13, color: "#5b6478" }}>请使用企业账号登录，运营侧数据与买家侧相互隔离。</p>

          <form onSubmit={submit}>
            {/* 账号 */}
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: "block", fontSize: 12.5, color: "#5b6478", marginBottom: 6 }}>账号</label>
              <div style={{ position: "relative" }}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#99a1b3" strokeWidth="2"
                  style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)" }}>
                  <circle cx="12" cy="8" r="4" /><path d="M4 21c0-4 3.6-6 8-6s8 2 8 6" />
                </svg>
                <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username"
                  style={{ ...inputStyle, paddingLeft: 38 }} />
              </div>
            </div>

            {/* 密码 */}
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: "block", fontSize: 12.5, color: "#5b6478", marginBottom: 6 }}>密码</label>
              <div style={{ position: "relative" }}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#99a1b3" strokeWidth="2"
                  style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)" }}>
                  <rect x="4" y="10" width="16" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3" />
                </svg>
                <input type={showPwd ? "text" : "password"} value={password}
                  onChange={(e) => setPassword(e.target.value)} autoComplete="current-password"
                  style={{ ...inputStyle, paddingLeft: 38, paddingRight: 54 }} />
                <button type="button" onClick={() => setShowPwd(!showPwd)} style={{
                  position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)",
                  border: 0, background: "transparent", cursor: "pointer", color: "#99a1b3", fontSize: 12, padding: 6,
                }}>{showPwd ? "隐藏" : "显示"}</button>
              </div>
            </div>

            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", margin: "4px 0 22px", fontSize: 12.5 }}>
              <label style={{ display: "flex", alignItems: "center", gap: 7, color: "#5b6478", cursor: "pointer" }}>
                <input type="checkbox" defaultChecked style={{ accentColor: "#0d6efd" }} /> 记住我
              </label>
              <a href="#" style={{ color: "#0d6efd", textDecoration: "none" }}>忘记密码？</a>
            </div>

            <button type="submit" disabled={loading} style={{
              width: "100%", padding: 12, fontSize: 15, fontWeight: 600, color: "#fff",
              background: "linear-gradient(180deg,#2b83ff,#0b62e6)", border: 0, borderRadius: 10,
              letterSpacing: 1, cursor: loading ? "not-allowed" : "pointer", opacity: loading ? 0.7 : 1,
              boxShadow: "0 8px 20px rgba(13,110,253,.28)", fontFamily: "inherit",
            }}>{loading ? "登录中..." : "登 录"}</button>

            {/* 错误条 */}
            {error && (
              <div style={{
                display: "flex", alignItems: "center", gap: 8, marginTop: 16, padding: "10px 12px",
                background: "#fdecee", border: "1px solid rgba(220,53,69,.28)", color: "#dc3545",
                borderRadius: 9, fontSize: 12.5,
              }}>
                <span>⚠️</span><span>{error}</span>
              </div>
            )}
          </form>

          <div style={{ display: "flex", alignItems: "center", gap: 12, margin: "26px 0 18px", color: "#99a1b3", fontSize: 11.5 }}>
            <span style={{ flex: 1, height: 1, background: "#e6e9f0" }} />或<span style={{ flex: 1, height: 1, background: "#e6e9f0" }} />
          </div>

          {/* 演示账号一键填充 */}
          <div style={{ fontSize: 12, color: "#5b6478", marginBottom: 9 }}>
            演示账号 <span style={{ color: "#99a1b3" }}>· 点击一键填充</span>
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {DEMOS.map((d) => {
              const on = activeDemo === d.u;
              return (
                <button key={d.u} type="button" onClick={() => fillDemo(d)} style={{
                  cursor: "pointer", textAlign: "left", fontFamily: "inherit", fontSize: 12, lineHeight: 1.45,
                  padding: "8px 11px", borderRadius: 9, transition: ".16s",
                  background: on ? "rgba(13,110,253,.08)" : "#f7f9fc",
                  border: `1px solid ${on ? "#0d6efd" : "#dfe4ee"}`,
                  boxShadow: on ? "0 0 0 3px rgba(13,110,253,.10)" : "none",
                  color: "#5b6478",
                }}>
                  <b style={{ display: "block", fontSize: 12.5, color: on ? "#0d6efd" : "#1a1d24" }}>{d.label}</b>
                  {d.u} / {d.p}
                </button>
              );
            })}
          </div>

          <div style={{
            marginTop: "auto", paddingTop: 26, display: "flex", alignItems: "center",
            justifyContent: "space-between", fontSize: 11.5, color: "#99a1b3",
          }}>
            <span>买家请前往 <a href="/buyer" style={{ color: "#0d6efd", textDecoration: "none", fontWeight: 600 }}>米家 →</a></span>
            <span>v4.0 · 内部演示环境</span>
          </div>
        </main>
      </div>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  width: "100%", padding: "11px 12px", fontSize: 14, color: "#1a1d24",
  background: "#f7f9fc", border: "1px solid #dfe4ee", borderRadius: 10,
  outline: "none", boxSizing: "border-box", fontFamily: "inherit",
};
