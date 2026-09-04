/** 商品详情：大图 + 信息 + 数量选择 + 加入购物车/立即购买。 */
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { getBuyer } from "../../api/client";
import { addToCart, getProduct } from "../../api/mockBuyer";
import { btnGhost, btnPrimary, card, fen } from "../../theme";

export default function ProductDetailPage() {
  const { id } = useParams();
  const product = getProduct(id ?? "");
  const [qty, setQty] = useState(1);
  const [message, setMessage] = useState("");
  const navigate = useNavigate();

  if (!product) {
    return (
      <p style={{ padding: 24 }}>
        商品不存在。<Link to="/buyer" style={{ color: "#0d6efd" }}>返回商城</Link>
      </p>
    );
  }

  function requireLogin(): boolean {
    if (!getBuyer()) {
      navigate("/buyer/login");
      return false;
    }
    return true;
  }

  function add() {
    if (!requireLogin()) return;
    addToCart(getBuyer()!.phone, product!.id, qty);
    setMessage(`已加入购物车 × ${qty}`);
  }

  function buyNow() {
    if (!requireLogin()) return;
    addToCart(getBuyer()!.phone, product!.id, qty);
    navigate("/buyer/checkout");
  }

  return (
    <div>
      <Link to="/buyer" style={{ fontSize: 13, color: "#0d6efd" }}>← 返回商品列表</Link>
      <div style={{ ...card, display: "grid", gridTemplateColumns: "320px 1fr", gap: 24, marginTop: 12 }}>
        <div style={{ height: 320, background: "#fff", border: "1px solid #f0f0f0", borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <img src={product.image} alt={product.name} style={{ maxWidth: "85%", maxHeight: "85%", objectFit: "contain" }} />
        </div>
        <div>
          <h1 style={{ fontSize: 22, margin: "4px 0" }}>{product.name}</h1>
          <p style={{ fontSize: 13, color: "#999" }}>分类：{product.category} · 库存 {product.stock}</p>
          <p style={{ color: "#dc3545", fontWeight: 700, fontSize: 28, margin: "12px 0" }}>{fen(product.price)}</p>
          <p style={{ fontSize: 14, color: "#555", lineHeight: 1.7, whiteSpace: "pre-wrap" }}>{product.desc}</p>

          <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 20 }}>
            <span style={{ fontSize: 14 }}>数量</span>
            <button onClick={() => setQty(Math.max(1, qty - 1))} style={qtyBtn}>-</button>
            <span style={{ minWidth: 32, textAlign: "center", fontWeight: 600 }}>{qty}</span>
            <button onClick={() => setQty(Math.min(product.stock, qty + 1))} style={qtyBtn}>+</button>
          </div>

          <div style={{ display: "flex", gap: 12, marginTop: 24 }}>
            <button onClick={add} style={btnGhost}>加入购物车</button>
            <button onClick={buyNow} style={btnPrimary}>立即购买</button>
          </div>
          {message && <p style={{ color: "#1a7f37", fontSize: 13, marginTop: 10 }}>{message}</p>}
        </div>
      </div>
    </div>
  );
}

const qtyBtn: React.CSSProperties = {
  width: 30,
  height: 30,
  border: "1px solid #ccc",
  borderRadius: 6,
  background: "#fff",
  cursor: "pointer",
  fontSize: 16,
};
