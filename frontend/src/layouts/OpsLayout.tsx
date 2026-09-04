/** 运营侧布局：左侧六域导航 + 顶栏（当前账号/角色 + 退出），右侧 <Outlet/>。 */
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { clearAuth, getDisplayName, getRole } from "../api/client";
import { BG } from "../theme";

const NAV = [
  { to: "/", label: "决策工作台", icon: "🛡", end: true },
  { to: "/batch", label: "批量审批", icon: "📦", managerOnly: true },
  { to: "/eval", label: "评测中心", icon: "🧪" },
  { to: "/security", label: "安全中心", icon: "🔒" },
  { to: "/intent", label: "意图监控", icon: "🎯" },
  { to: "/telemetry", label: "系统监控", icon: "📈" },
];

const ROLE_LABEL: Record<string, string> = {
  ADMIN: "系统管理员",
  MANAGER: "客服主管",
  CSR: "客服",
};

export default function OpsLayout() {
  const navigate = useNavigate();
  const role = getRole() ?? "";
  const name = getDisplayName() ?? "";

  function logout() {
    clearAuth();
    navigate("/login");
  }

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: BG }}>
      {/* 侧边栏 */}
      <aside style={{ width: 200, background: "#1c2333", color: "#cfd6e4", display: "flex", flexDirection: "column", flexShrink: 0 }}>
        <div style={{ padding: "18px 16px", borderBottom: "1px solid #2c3547" }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: "#fff" }}>客诉退赔决策系统</div>
          <div style={{ fontSize: 11, color: "#7d8aa5", marginTop: 4 }}>多Agent 协同 · 运营侧</div>
        </div>
        <nav style={{ flex: 1, padding: "10px 8px" }}>
          {NAV.filter((n) => !n.managerOnly || role === "MANAGER" || role === "ADMIN").map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              style={({ isActive }) => ({
                display: "block",
                padding: "10px 12px",
                borderRadius: 8,
                marginBottom: 4,
                fontSize: 14,
                textDecoration: "none",
                color: isActive ? "#fff" : "#cfd6e4",
                background: isActive ? "#0d6efd" : "transparent",
              })}
            >
              {n.icon} {n.label}
            </NavLink>
          ))}
        </nav>
        <div style={{ padding: "12px 16px", borderTop: "1px solid #2c3547", fontSize: 12, color: "#7d8aa5" }}>
          <a href="/buyer" style={{ color: "#7d8aa5", textDecoration: "none" }}>→ 前往买家商城</a>
        </div>
      </aside>

      {/* 主区 */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        <header
          style={{
            height: 52,
            background: "#fff",
            borderBottom: "1px solid #eee",
            display: "flex",
            alignItems: "center",
            justifyContent: "flex-end",
            padding: "0 20px",
            gap: 12,
            flexShrink: 0,
          }}
        >
          <span style={{ fontSize: 13, color: "#555" }}>
            {name}（{ROLE_LABEL[role] ?? role}）
          </span>
          <button
            onClick={logout}
            style={{ padding: "5px 14px", border: "1px solid #ccc", borderRadius: 6, background: "#fff", cursor: "pointer", fontSize: 13 }}
          >
            退出登录
          </button>
        </header>
        <main style={{ flex: 1, overflowY: "auto" }}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
