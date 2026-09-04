/** 结算页：订单确认 + 模拟支付（生成订单、清空购物车）。
 *  v2.0 文档 P2：虚拟交易场景，结算页只确认商品+总价，不收收货地址。 */
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { getBuyer } from "../../api/client";
import { clearCart, createOrder, getCart, getProduct, payOrder } from "../../api/mockBuyer";
import { btnPrimary, card, fen } from "../../theme";

export default function CheckoutPage() {
  const buyer = getBuyer();
  const navigate = useNavigate();
  const [paying, setPaying] = useState(false);

  if (!buyer) {
    return (
      <p style={{ padding: 24 }}>
        请先 <Link to="/buyer/login" style={{ color: "#0d6efd" }}>登录</Link> 后结算。
      </p>
    );
  }

  const items = getCart(buyer.phone)
    .map((i) => ({ ...i, product: getProduct(i.product_id)! }))
    .filter((i) => i.product);
  const total = items.reduce((s, i) => s + i.product.price * i.qty, 0);

  async function pay() {
    setPaying(true);
    try {
      const order = await createOrder(
        buyer!.phone,
        items.map((i) => ({ product_id: i.product_id, name: i.product.name, price: i.product.price, qty: i.qty })),
      );
      // 模拟支付：创建订单后立即调后端 PAY 接口置为 PAID，状态以后端为准
      await payOrder(order.id);
      clearCart(buyer!.phone);
      navigate(`/buyer/orders?paid=${order.id}`);
    } catch (err) {
      // 会话过期时 buyerRequest 会自动清理登录态并跳转登录页；
      // 其它失败（如购物车已空/金额变更）必须在这里可见，否则"支付"静默无反应。
      alert(err instanceof Error ? err.message : "支付失败，请稍后再试");
    } finally {
      setPaying(false);
    }
  }

  if (items.length === 0) {
    return (
      <div style={{ ...card, textAlign: "center", padding: 48, color: "#888" }}>
        没有待结算的商品，<Link to="/buyer" style={{ color: "#0d6efd" }}>去逛逛 →</Link>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 640, margin: "0 auto" }}>
      <h1 style={{ fontSize: 20, margin: "0 0 16px" }}>🧾 确认订单</h1>

      <section style={{ ...card, marginBottom: 14 }}>
        <h2 style={{ fontSize: 15, margin: "0 0 10px" }}>商品清单</h2>
        {items.map((i) => (
          <div key={i.product_id} style={{ display: "flex", justifyContent: "space-between", fontSize: 14, padding: "6px 0", borderBottom: "1px dashed #eee" }}>
            <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <img src={i.product.image} alt="" style={{ width: 28, height: 28, objectFit: "contain" }} />
              {i.product.name} × {i.qty}
            </span>
            <span>{fen(i.product.price * i.qty)}</span>
          </div>
        ))}
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 12, fontSize: 15 }}>
          <span>应付总额</span>
          <span style={{ color: "#dc3545", fontWeight: 700, fontSize: 20 }}>{fen(total)}</span>
        </div>
      </section>

      <section style={{ ...card, marginBottom: 14 }}>
        <h2 style={{ fontSize: 15, margin: "0 0 10px" }}>支付方式</h2>
        <div style={{ display: "flex", gap: 10 }}>
          <div style={{ flex: 1, border: "2px solid #0d6efd", background: "#e7f1ff", borderRadius: 8, padding: 12, fontSize: 14, textAlign: "center" }}>
            模拟支付（演示环境）
          </div>
        </div>
      </section>

      <button onClick={pay} disabled={paying} style={{ ...btnPrimary, width: "100%", padding: 12, fontSize: 16 }}>
        {paying ? "支付中..." : `确认支付 ${fen(total)}`}
      </button>
    </div>
  );
}
