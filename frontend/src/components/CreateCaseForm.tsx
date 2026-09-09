/** 外部渠道代录建单：电话/邮件等外部渠道进来的客诉，由客服代为录入（买家线上自助售后由系统自动建单，不走这里）。
 *  工单8 交叉校验：与买家端一致，售后类型显式三选一（退款/退货退款/换货）随 claim_type 落库，
 *  意图识别以用户显式选择为主通道、与双层识别交叉校验（避免默认「退款」吞掉换货诉求）。
 *  金额/描述/凭证图片上传（multipart + 幂等键）；换货不涉及资金，金额强制 0。
 */
import { useRef, useState } from "react";
import { genIdempotencyKey } from "../api/client";

type AfterSaleType = "退款" | "退货退款" | "换货";

const TYPES: { type: AfterSaleType; icon: string; desc: string }[] = [
  { type: "退款", icon: "💰", desc: "仅退钱" },
  { type: "退货退款", icon: "📦", desc: "寄回商品并退款" },
  { type: "换货", icon: "🔄", desc: "换新 · 转人工" },
];

export default function CreateCaseForm({ onCreated }: { onCreated: () => void }) {
  const applicantId = "user-demo";
  const [type, setType] = useState<AfterSaleType | null>(null);
  const [orderId, setOrderId] = useState("");
  const [amount, setAmount] = useState("");
  const [description, setDescription] = useState("");
  const [image, setImage] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setMessage("");
    // 工单8 交叉校验：必须显式三选一，不允许默认「退款」吞掉换货/退货诉求
    if (!type) {
      setError("请先选择售后类型（退款 / 退货退款 / 换货）");
      return;
    }
    // 换货不涉及资金 → 金额 0；退款/退货退款必须填正数
    if (type !== "换货") {
      const a = parseFloat(amount);
      if (!Number.isFinite(a) || a <= 0) {
        setError("请填写正确的退款金额（元）");
        return;
      }
    }
    setLoading(true);
    try {
      const finalAmount = type === "换货" ? 0 : Math.round(parseFloat(amount) * 100);
      const form = new FormData();
      form.append("applicant_id", applicantId);
      form.append("order_id", orderId);
      form.append("applicant_amount", String(finalAmount));
      form.append("actual_amount", String(finalAmount));
      form.append("claim_type", type); // 显式售后类型 → 意图识别交叉校验
      form.append("description", description);
      if (image) form.append("image", image);

      const resp = await fetch("/api/v1/cases", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${localStorage.getItem("refund_token")}`,
          "X-Idempotency-Key": genIdempotencyKey(),
        },
        body: form,
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.message ?? "提交失败");
      setMessage(`已受理：${data.ticket_no}，状态 ${data.status}（异步处理中）`);
      setType(null);
      setOrderId("");
      setAmount("");
      setDescription("");
      setImage(null);
      if (fileRef.current) fileRef.current.value = "";
      onCreated();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={submit} style={{ border: "1px solid #ddd", borderRadius: 8, padding: 16, background: "#fff" }}>
      <h3 style={{ margin: "0 0 4px", fontSize: 15 }}>📝 外部渠道代录</h3>
      <p style={{ margin: "0 0 12px", fontSize: 12, color: "#888" }}>
        电话/邮件等外部渠道客诉由客服代录；买家线上自助售后由系统自动建单
      </p>

      {/* 售后类型三选一（无默认值，必须显式声明） */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8, marginBottom: 10 }}>
        {TYPES.map((t) => (
          <button
            key={t.type}
            type="button"
            onClick={() => setType(t.type)}
            style={{
              padding: "8px 4px",
              borderRadius: 8,
              border: type === t.type ? "2px solid #0d6efd" : "1px solid #e0e0e0",
              background: type === t.type ? "#e7f1ff" : "#fff",
              cursor: "pointer",
              textAlign: "center",
            }}
          >
            <div style={{ fontSize: 16 }}>{t.icon}</div>
            <div style={{ fontSize: 13, fontWeight: 700, color: type === t.type ? "#0d6efd" : "#333" }}>{t.type}</div>
            <div style={{ fontSize: 10, color: "#999" }}>{t.desc}</div>
          </button>
        ))}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        <label style={{ fontSize: 13 }}>
          订单号
          <input value={orderId} required onChange={(e) => setOrderId(e.target.value)} style={inputStyle} placeholder="O2026… 真实订单号" />
          <span style={{ fontSize: 11, color: "#999" }}>在买家端「我的订单」页复制真实订单号，伪订单会被三查拦截</span>
        </label>
        {/* 换货不涉及资金 → 隐藏金额框，提交时金额为 0 */}
        {type !== "换货" ? (
          <label style={{ fontSize: 13 }}>
            退款金额（元）
            <input value={amount} required type="number" min="0" step="0.01" onChange={(e) => setAmount(e.target.value)} style={inputStyle} placeholder="如 350.00" />
          </label>
        ) : (
          <label style={{ fontSize: 13 }}>
            退款金额（元）
            <div style={{ ...inputStyle, background: "#f5f6fa", color: "#999", display: "flex", alignItems: "center" }}>换货 · 无需金额</div>
          </label>
        )}
      </div>
      <label style={{ fontSize: 13, display: "block", marginTop: 10 }}>
        客诉描述
        <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} style={{ ...inputStyle, width: "100%" }} placeholder="商品破损，申请退款" />
      </label>
      <label style={{ fontSize: 13, display: "block", marginTop: 10 }}>
        凭证图片（破损照片/快递单/发票）
        <input ref={fileRef} type="file" accept="image/*" onChange={(e) => setImage(e.target.files?.[0] ?? null)} style={{ display: "block", marginTop: 4 }} />
      </label>
      <button type="submit" disabled={loading} style={{ marginTop: 12, padding: "8px 24px", background: "#0d6efd", color: "#fff", border: "none", borderRadius: 6, cursor: "pointer" }}>
        {loading ? "提交中..." : "代录工单"}
      </button>
      {error && <p style={{ color: "#dc3545", fontSize: 13, marginTop: 8 }}>{error}</p>}
      {message && <p style={{ color: "#1a7f37", fontSize: 13, marginTop: 8 }}>{message}</p>}
    </form>
  );
}

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: 7,
  borderRadius: 6,
  border: "1px solid #ccc",
  marginTop: 4,
  boxSizing: "border-box",
};
