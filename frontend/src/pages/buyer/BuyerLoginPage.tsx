/** 买家登录/注册（mock）：手机号 + 密码；后端 customer_auth 落地后换真 JWT。 */
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { setBuyer } from "../../api/client";
import { loginBuyer, registerBuyer } from "../../api/mockBuyer";
import { btnPrimary, input } from "../../theme";

export default function BuyerLoginPage() {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [nickname, setNickname] = useState("");
  const [error, setError] = useState("");
  const navigate = useNavigate();

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!/^1\d{10}$/.test(phone)) {
      setError("请输入 11 位手机号");
      return;
    }
    if (password.length < 6) {
      setError("密码至少 6 位");
      return;
    }
    if (mode === "register") {
      const err = await registerBuyer(phone, password, nickname || `买家${phone.slice(-4)}`);
      if (err) {
        setError(err);
        return;
      }
    }
    const result = await loginBuyer(phone, password);
    if (typeof result === "string") {
      setError(mode === "register" ? result : "手机号或密码错误（新用户请先注册）");
      return;
    }
    setBuyer({ phone: result.phone, nickname: result.nickname });
    navigate("/buyer");
  }

  return (
    <div style={{ display: "flex", justifyContent: "center", paddingTop: 48 }}>
      <form onSubmit={submit} style={{ width: 340, padding: 32, border: "1px solid #ddd", borderRadius: 12, background: "#fff" }}>
        <h1 style={{ fontSize: 20, marginBottom: 4 }}>米家</h1>
        <p style={{ fontSize: 13, color: "#777", marginBottom: 20 }}>买家登录 · 自助售后由系统自动建单</p>

        <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
          {(["login", "register"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => { setMode(m); setError(""); }}
              style={{
                flex: 1,
                padding: 8,
                borderRadius: 6,
                border: mode === m ? "2px solid #0d6efd" : "1px solid #ccc",
                background: mode === m ? "#e7f1ff" : "#fff",
                cursor: "pointer",
                fontSize: 14,
              }}
            >
              {m === "login" ? "登录" : "注册"}
            </button>
          ))}
        </div>

        <label style={{ fontSize: 13, display: "block" }}>
          手机号
          <input value={phone} onChange={(e) => setPhone(e.target.value)} style={input} placeholder="13812345678" />
        </label>
        {mode === "register" && (
          <label style={{ fontSize: 13, display: "block", marginTop: 12 }}>
            昵称（选填）
            <input value={nickname} onChange={(e) => setNickname(e.target.value)} style={input} placeholder="铁牛" />
          </label>
        )}
        <label style={{ fontSize: 13, display: "block", marginTop: 12 }}>
          密码
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} style={input} placeholder="至少 6 位" />
        </label>

        <button type="submit" style={{ ...btnPrimary, width: "100%", marginTop: 20, padding: 10 }}>
          {mode === "login" ? "登录" : "注册并登录"}
        </button>
        {error && <p style={{ color: "#dc3545", fontSize: 13, marginTop: 10 }}>{error}</p>}
        <p style={{ fontSize: 12, color: "#999", marginTop: 16 }}>
          当前为演示环境：账号存于浏览器本地，后端 buyer API 落地后切换为真实认证
        </p>
      </form>
    </div>
  );
}
