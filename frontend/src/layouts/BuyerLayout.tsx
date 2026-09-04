/** 买家侧布局：顶部导航（商城 / 购物车 / 我的订单 / 我的售后）+ 登录状态 + <Outlet/>。 */
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { clearBuyer, getBuyer } from "../api/client";
import { cartCount } from "../api/mockBuyer";
import BuyerChatWidget from "../components/BuyerChatWidget";
import { BG } from "../theme";

export default function BuyerLayout() {
  const buyer = getBuyer();
  const navigate = useNavigate();

  function logout() {
    clearBuyer();
    navigate("/buyer/login");
  }

  const navStyle = ({ isActive }: { isActive: boolean }) => ({
    fontSize: 14,
    textDecoration: "none",
    color: isActive ? "#0d6efd" : "#555",
    fontWeight: isActive ? 600 : 400,
  });

  return (
    <div style={{ minHeight: "100vh", background: BG }}>
      <header
        style={{
          background: "#fff",
          borderBottom: "1px solid #eee",
          display: "flex",
          alignItems: "center",
          padding: "0 24px",
          height: 56,
          gap: 28,
          position: "sticky",
          top: 0,
          zIndex: 10,
        }}
      >
        <Link to="/buyer" style={{ fontSize: 17, fontWeight: 700, color: "#0d6efd", textDecoration: "none" }}>
          米家
        </Link>
        <nav style={{ display: "flex", gap: 20, flex: 1 }}>
          <NavLink to="/buyer" end style={navStyle}>商品</NavLink>
          <NavLink to="/buyer/cart" style={navStyle}>
            购物车{buyer && cartCount(buyer.phone) > 0 ? `（${cartCount(buyer.phone)}）` : ""}
          </NavLink>
          <NavLink to="/buyer/orders" style={navStyle}>我的订单</NavLink>
          <NavLink to="/buyer/aftersale" style={navStyle}>我的售后</NavLink>
        </nav>
        {buyer ? (
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span style={{ fontSize: 13, color: "#555" }}>{buyer.nickname}（{buyer.phone}）</span>
            <button onClick={logout} style={{ padding: "5px 12px", border: "1px solid #ccc", borderRadius: 6, background: "#fff", cursor: "pointer", fontSize: 13 }}>
              退出
            </button>
          </div>
        ) : (
          <Link to="/buyer/login" style={{ fontSize: 13, color: "#0d6efd", textDecoration: "none" }}>登录 / 注册</Link>
        )}
      </header>
      <main style={{ maxWidth: 1000, margin: "0 auto", padding: 24 }}>
        <Outlet />
      </main>
      {/* 全局悬浮智能客服（买家任意页面可用；未登录也可咨询政策，数据型问题给登录引导） */}
      <BuyerChatWidget />
    </div>
  );
}
