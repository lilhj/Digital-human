/** 用户管理（ADMIN 专属，phase12）：员工账号 + 买家账号的生命周期管理。
 *  密码一律不可逆哈希存储，本页不提供任何"查看明文密码"能力，只支持重置（新密码覆盖旧密码）。
 *  仅 ADMIN 角色可见；后端 require_roles(ROLE_ADMIN) 兜底 403。
 */
import { useCallback, useEffect, useState } from "react";
import {
  createAdminUser,
  getAdminCustomers,
  getAdminUsers,
  getRole,
  resetCustomerPassword,
  resetUserPassword,
  toggleCustomerActive,
  updateAdminUser,
} from "../../api/client";
import type { AdminCustomer, AdminUser } from "../../api/types";
import { card, td, th } from "../../theme";

export default function AdminUsersPage() {
  const isAdmin = getRole() === "ADMIN";

  const [users, setUsers] = useState<AdminUser[]>([]);
  const [customers, setCustomers] = useState<AdminCustomer[]>([]);
  const [error, setError] = useState("");

  // 新增员工表单
  const [nu, setNu] = useState({ username: "", password: "", role: "CSR", display_name: "" });
  // 内联重置密码：{ kind: 'user'|'customer', id } -> 输入框
  const [resetting, setResetting] = useState<{ kind: "user" | "customer"; id: number } | null>(null);
  const [pwd, setPwd] = useState("");

  const load = useCallback(async () => {
    try {
      const [u, c] = await Promise.all([getAdminUsers(), getAdminCustomers()]);
      setUsers(u);
      setCustomers(c);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    if (isAdmin) load().catch(() => {});
  }, [isAdmin, load]);

  if (!isAdmin) {
    return (
      <div style={{ maxWidth: 900, margin: "0 auto", padding: 24 }}>
        <h1 style={{ fontSize: 20, margin: "0 0 16px" }}>👥 用户管理</h1>
        <div style={{ ...card, padding: 24, textAlign: "center", color: "#dc3545" }}>
          无权限：本页面仅系统管理员（ADMIN）可访问。
        </div>
      </div>
    );
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await createAdminUser(nu);
      setNu({ username: "", password: "", role: "CSR", display_name: "" });
      await load();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function handleToggleActive(id: number) {
    try {
      await updateAdminUser(id, { is_active: false });
      await load();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function handleRoleChange(id: number, role: string) {
    try {
      await updateAdminUser(id, { role });
      await load();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function handleReset() {
    if (!resetting || pwd.length < 6) return;
    try {
      if (resetting.kind === "user") await resetUserPassword(resetting.id, pwd);
      else await resetCustomerPassword(resetting.id, pwd);
      setResetting(null);
      setPwd("");
      setError("");
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function handleToggleCustomer(id: number) {
    try {
      await toggleCustomerActive(id);
      await load();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: 24 }}>
      <h1 style={{ fontSize: 20, margin: "0 0 16px" }}>👥 用户管理（管理员专属）</h1>
      <p style={{ fontSize: 12, color: "#888", marginTop: -8 }}>
        密码全程哈希存储、不可逆查看；此处仅提供账号生命周期管理（新增 / 改角色 / 启停用 / 重置密码）。
      </p>

      {error && (
        <div style={{ ...card, borderColor: "#dc3545", background: "#fff5f5", marginBottom: 16, fontSize: 13, color: "#a31d1d" }}>
          {error}
        </div>
      )}

      {/* ---------- 员工管理 ---------- */}
      <div style={{ ...card, marginBottom: 20, padding: 16 }}>
        <h2 style={{ fontSize: 16, margin: "0 0 12px" }}>员工账号</h2>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ background: "#f5f6fa" }}>
              <th style={th}>用户名</th>
              <th style={th}>角色</th>
              <th style={th}>显示名</th>
              <th style={th}>状态</th>
              <th style={th}>创建时间</th>
              <th style={th}>操作</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} style={{ borderBottom: "1px solid #eee" }}>
                <td style={td}>{u.username}</td>
                <td style={td}>
                  <select
                    value={u.role}
                    onChange={(e) => handleRoleChange(u.id, e.target.value)}
                    style={{ padding: "3px 6px", fontSize: 12 }}
                  >
                    <option value="CSR">客服</option>
                    <option value="MANAGER">主管</option>
                    <option value="ADMIN">管理员</option>
                  </select>
                </td>
                <td style={td}>{u.display_name}</td>
                <td style={td}>
                  {u.is_active ? (
                    <span style={{ color: "#1a7f37" }}>启用</span>
                  ) : (
                    <span style={{ color: "#dc3545" }}>停用</span>
                  )}
                </td>
                <td style={td}>{new Date(u.created_at).toLocaleString()}</td>
                <td style={td}>
                  <button
                    onClick={() => { setResetting({ kind: "user", id: u.id }); setPwd(""); }}
                    style={btnStyle}
                  >
                    重置密码
                  </button>
                  {u.is_active ? (
                    <button onClick={() => handleToggleActive(u.id)} style={{ ...btnStyle, background: "#dc3545" }}>
                      停用
                    </button>
                  ) : (
                    <button
                      onClick={() => updateAdminUser(u.id, { is_active: true }).then(load)}
                      style={{ ...btnStyle, background: "#28a745" }}
                    >
                      启用
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {/* 新增员工 */}
        <form onSubmit={handleCreate} style={{ display: "flex", gap: 8, marginTop: 14, flexWrap: "wrap", alignItems: "center" }}>
          <span style={{ fontSize: 13, color: "#555" }}>新增员工：</span>
          <input value={nu.username} required placeholder="用户名" onChange={(e) => setNu({ ...nu, username: e.target.value })} style={inpStyle} />
          <input value={nu.password} required minLength={6} type="password" placeholder="初始密码(≥6)" onChange={(e) => setNu({ ...nu, password: e.target.value })} style={inpStyle} />
          <input value={nu.display_name} required placeholder="显示名" onChange={(e) => setNu({ ...nu, display_name: e.target.value })} style={inpStyle} />
          <select value={nu.role} onChange={(e) => setNu({ ...nu, role: e.target.value })} style={{ padding: 7, fontSize: 13 }}>
            <option value="CSR">客服</option>
            <option value="MANAGER">主管</option>
            <option value="ADMIN">管理员</option>
          </select>
          <button type="submit" style={{ padding: "7px 16px", background: "#0d6efd", color: "#fff", border: "none", borderRadius: 6, cursor: "pointer" }}>
            创建账号
          </button>
        </form>
      </div>

      {/* ---------- 买家管理 ---------- */}
      <div style={{ ...card, padding: 16 }}>
        <h2 style={{ fontSize: 16, margin: "0 0 12px" }}>买家账号</h2>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ background: "#f5f6fa" }}>
              <th style={th}>手机号</th>
              <th style={th}>昵称</th>
              <th style={th}>状态</th>
              <th style={th}>注册时间</th>
              <th style={th}>操作</th>
            </tr>
          </thead>
          <tbody>
            {customers.length === 0 ? (
              <tr>
                <td colSpan={5} style={{ ...td, color: "#888" }}>暂无买家</td>
              </tr>
            ) : (
              customers.map((c) => (
                <tr key={c.id} style={{ borderBottom: "1px solid #eee" }}>
                  <td style={td}>{c.phone}</td>
                  <td style={td}>{c.nickname ?? "-"}</td>
                  <td style={td}>
                    {c.is_active ? (
                      <span style={{ color: "#1a7f37" }}>正常</span>
                    ) : (
                      <span style={{ color: "#dc3545" }}>已停用</span>
                    )}
                  </td>
                  <td style={td}>{new Date(c.created_at).toLocaleString()}</td>
                  <td style={td}>
                    <button onClick={() => { setResetting({ kind: "customer", id: c.id }); setPwd(""); }} style={btnStyle}>
                      重置密码
                    </button>
                    <button onClick={() => handleToggleCustomer(c.id)} style={{ ...btnStyle, background: c.is_active ? "#dc3545" : "#28a745" }}>
                      {c.is_active ? "停用" : "启用"}
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* 内联重置密码输入 */}
      {resetting && (
        <div style={{ ...card, marginTop: 16, padding: 16, borderColor: "#ffc107", background: "#fffdf5" }}>
          <div style={{ fontSize: 13, marginBottom: 8, color: "#8a6d00" }}>
            重置{resetting.kind === "user" ? "员工" : "买家"} #{resetting.id} 的密码（旧密码立即失效，新密码 ≥ 6 位）
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              autoFocus
              type="password"
              value={pwd}
              minLength={6}
              placeholder="新密码（≥6 位）"
              onChange={(e) => setPwd(e.target.value)}
              style={inpStyle}
              onKeyDown={(e) => { if (e.key === "Enter") void handleReset(); }}
            />
            <button onClick={handleReset} disabled={pwd.length < 6} style={{ padding: "7px 16px", background: "#0d6efd", color: "#fff", border: "none", borderRadius: 6, cursor: "pointer" }}>
              确认重置
            </button>
            <button onClick={() => setResetting(null)} style={{ padding: "7px 16px", background: "#fff", border: "1px solid #ccc", borderRadius: 6, cursor: "pointer" }}>
              取消
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

const btnStyle: React.CSSProperties = {
  padding: "4px 10px",
  fontSize: 12,
  background: "#0d6efd",
  color: "#fff",
  border: "none",
  borderRadius: 5,
  cursor: "pointer",
  marginRight: 6,
};

const inpStyle: React.CSSProperties = {
  padding: 7,
  borderRadius: 6,
  border: "1px solid #ccc",
  fontSize: 13,
};
