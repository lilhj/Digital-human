/** 购物车：数量增减 / 删除 / 合计 / 去结算。 */
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { getBuyer } from "../../api/client";
import { getCart, getProduct, updateCartQty } from "../../api/mockBuyer";
import { btnPrimary, card, fen } from "../../theme";

export default function CartPage() {
  const buyer = getBuyer();
  const navigate = useNavigate();
  const [, force] = useState(0);
  const refresh = () => force((n) => n + 1);

  if (!buyer) {
    return (
      <p style={{ padding: 24 }}>
        请先 <Link to="/buyer/login" style={{ color: "#0d6efd" }}>登录</Link> 后查看购物车。
      </p>
    );
  }

  const items = getCart(buyer.phone)
    .map((i) => ({ ...i, product: getProduct(i.product_id)! }))
    .filter((i) => i.product);
  const total = items.reduce((s, i) => s + i.product.price * i.qty, 0);

  function setQty(productId: string, qty: number) {
    updateCartQty(buyer!.phone, productId, qty);
    refresh();
  }

  return (
    <div>
      <h1 style={{ fontSize: 20, margin: "0 0 16px" }}>🛒 购物车</h1>
      {items.length === 0 ? (
        <div style={{ ...card, textAlign: "center", padding: 48, color: "#888" }}>
          购物车是空的，<Link to="/buyer" style={{ color: "#0d6efd" }}>去逛逛 →</Link>
        </div>
      ) : (
        <>
          {items.map((i) => (
            <div key={i.product_id} style={{ ...card, display: "flex", alignItems: "center", gap: 16, marginBottom: 10 }}>
              <div style={{ width: 64, height: 64, background: "#fff", border: "1px solid #f0f0f0", borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                <img src={i.product.image} alt={i.product.name} style={{ maxWidth: "85%", maxHeight: "85%", objectFit: "contain" }} />
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 14, fontWeight: 600 }}>{i.product.name}</div>
                <div style={{ fontSize: 13, color: "#dc3545", fontWeight: 600, marginTop: 4 }}>{fen(i.product.price)}</div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <button onClick={() => setQty(i.product_id, i.qty - 1)} style={qtyBtn}>-</button>
                <span style={{ minWidth: 28, textAlign: "center" }}>{i.qty}</span>
                <button onClick={() => setQty(i.product_id, i.qty + 1)} style={qtyBtn}>+</button>
              </div>
              <div style={{ width: 100, textAlign: "right", fontWeight: 600 }}>{fen(i.product.price * i.qty)}</div>
              <button onClick={() => setQty(i.product_id, 0)} style={{ border: "none", background: "none", color: "#dc3545", cursor: "pointer", fontSize: 13 }}>
                删除
              </button>
            </div>
          ))}

          <div style={{ ...card, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: 14 }}>
              合计：<span style={{ color: "#dc3545", fontWeight: 700, fontSize: 20 }}>{fen(total)}</span>
            </span>
            <button onClick={() => navigate("/buyer/checkout")} style={{ ...btnPrimary, padding: "10px 32px" }}>
              去结算
            </button>
          </div>
        </>
      )}
    </div>
  );
}

const qtyBtn: React.CSSProperties = {
  width: 26,
  height: 26,
  border: "1px solid #ccc",
  borderRadius: 6,
  background: "#fff",
  cursor: "pointer",
};
