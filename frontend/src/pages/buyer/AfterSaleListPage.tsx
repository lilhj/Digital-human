/** 我的售后：售后申请列表 + 工单状态跟踪。
 *  状态与处理结果以后端真实判定为准（GET /api/v1/buyer/cases 同步，不再依赖本地镜像）。 */
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { getBuyer } from "../../api/client";
import { getAfterSales, refreshOrders, syncAfterSales } from "../../api/mockBuyer";
import { card, fen, td, th } from "../../theme";

const STATUS_COLOR: Record<string, string> = {
  待处理: "#8a6d00",
  处理中: "#0d6efd",
  已完结: "#1a7f37",
};

export default function AfterSaleListPage() {
  const buyer = getBuyer();
  const [params] = useSearchParams();
  const created = params.get("created");
  const [, setTick] = useState(0);

  // 挂载即同步后端真实状态（售后判定结果/处理结果）与订单状态（退款完成后变已退款）
  useEffect(() => {
    let on = true;
    (async () => {
      if (!buyer) return;
      await syncAfterSales(buyer.phone);
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
        请先 <Link to="/buyer/login" style={{ color: "#0d6efd" }}>登录</Link> 后查看售后。
      </p>
    );
  }

  const list = getAfterSales(buyer.phone);

  return (
    <div>
      <h1 style={{ fontSize: 20, margin: "0 0 16px" }}>🛠 我的售后</h1>

      {created && (
        <div style={{ ...card, borderColor: "#28a745", background: "#f6fcf8", marginBottom: 16, fontSize: 14, color: "#1a7f37" }}>
          售后申请已提交，系统已自动建单并进入多Agent 决策链路，处理进度将实时更新。
        </div>
      )}

      {list.length === 0 ? (
        <div style={{ ...card, textAlign: "center", padding: 48, color: "#888" }}>
          暂无售后申请，可在 <Link to="/buyer/orders" style={{ color: "#0d6efd" }}>我的订单</Link> 中发起。
        </div>
      ) : (
        <section style={card}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ background: "#f5f6fa" }}>
                <th style={th}>申请时间</th>
                <th style={th}>订单号</th>
                <th style={th}>类型</th>
                <th style={th}>金额</th>
                <th style={th}>原因</th>
                <th style={th}>凭证</th>
                <th style={th}>工单号</th>
                <th style={th}>状态</th>
                <th style={th}>处理结果</th>
              </tr>
            </thead>
            <tbody>
              {list.map((a) => (
                <tr key={a.id} style={{ borderBottom: "1px solid #eee" }}>
                  <td style={td}>{new Date(a.created_at).toLocaleString("zh-CN")}</td>
                  <td style={td}>{a.order_id}</td>
                  <td style={td}>{a.type}</td>
                  <td style={td}>{a.type === "换货" ? "-" : fen(a.amount)}</td>
                  <td style={{ ...td, maxWidth: 200 }}>{a.reason}</td>
                  <td style={td}>
                    {a.evidence ? (
                      <a href={a.evidence} target="_blank" rel="noreferrer">
                        <img src={a.evidence} alt="凭证" style={{ width: 40, height: 40, objectFit: "cover", borderRadius: 4, border: "1px solid #eee" }} />
                      </a>
                    ) : (
                      <span style={{ color: "#bbb" }}>无</span>
                    )}
                  </td>
                  <td style={td}>{a.ticket_no}</td>
                  <td style={{ ...td, color: STATUS_COLOR[a.status], fontWeight: 600 }}>{a.status}</td>
                  <td style={{ ...td, maxWidth: 280, color: a.result ? "#555" : "#bbb" }}>
                    {a.result ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
