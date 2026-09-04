/** 我的订单：订单列表 + 申请售后入口。
 *  订单状态以后端为准：退款完成后变 REFUNDED，挂载时刷新避免展示陈旧缓存。 */
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { getBuyer } from "../../api/client";
import { getOrders, refreshOrders } from "../../api/mockBuyer";
import { card, fen, td, th } from "../../theme";

export default function OrdersPage() {
  const buyer = getBuyer();
  const [params] = useSearchParams();
  const paidId = params.get("paid");
  const [, setTick] = useState(0);

  // 挂载时刷新订单状态（售后完成后订单应显示"已退款"而不是缓存的旧态）
  useEffect(() => {
    let on = true;
    (async () => {
      if (!buyer) return;
      await refreshOrders();
      if (on) setTick((t) => t + 1);
    })();
    return () => {
      on = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!buyer) {
    return (
      <p style={{ padding: 24 }}>
        请先 <Link to="/buyer/login" style={{ color: "#0d6efd" }}>登录</Link> 后查看订单。
      </p>
    );
  }

  const orders = getOrders(buyer.phone);

  return (
    <div>
      <h1 style={{ fontSize: 20, margin: "0 0 16px" }}>📦 我的订单</h1>

      {paidId && (
        <div style={{ ...card, borderColor: "#28a745", background: "#f6fcf8", marginBottom: 16, fontSize: 14, color: "#1a7f37" }}>
          支付成功！订单 {paidId} 已创建。
        </div>
      )}

      {orders.length === 0 ? (
        <div style={{ ...card, textAlign: "center", padding: 48, color: "#888" }}>
          暂无订单，<Link to="/buyer" style={{ color: "#0d6efd" }}>去逛逛 →</Link>
        </div>
      ) : (
        orders.map((o) => (
          <section key={o.id} style={{ ...card, marginBottom: 14 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
              <div style={{ fontSize: 13, color: "#777" }}>
                订单号 <b style={{ color: "#333" }}>{o.id}</b> · {new Date(o.created_at).toLocaleString("zh-CN")}
              </div>
              <span style={{ fontSize: 13, color: "#0d6efd", fontWeight: 600 }}>{o.status}</span>
            </div>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ background: "#f5f6fa" }}>
                  <th style={th}>商品</th>
                  <th style={th}>单价</th>
                  <th style={th}>数量</th>
                  <th style={th}>小计</th>
                </tr>
              </thead>
              <tbody>
                {o.items.map((i) => (
                  <tr key={i.product_id} style={{ borderBottom: "1px solid #eee" }}>
                    <td style={td}>{i.name}</td>
                    <td style={td}>{fen(i.price)}</td>
                    <td style={td}>{i.qty}</td>
                    <td style={td}>{fen(i.price * i.qty)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 12 }}>
              <span style={{ fontSize: 14 }}>
                实付：<span style={{ color: "#dc3545", fontWeight: 700, fontSize: 18 }}>{fen(o.total)}</span>
              </span>
              <Link
                to={`/buyer/orders/${o.id}/after-sale`}
                style={{ padding: "7px 18px", border: "1px solid #0d6efd", color: "#0d6efd", borderRadius: 6, fontSize: 13, textDecoration: "none" }}
              >
                申请售后
              </Link>
            </div>
          </section>
        ))
      )}
    </div>
  );
}
