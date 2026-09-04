/** 外部渠道代录建单：电话/邮件等外部渠道进来的客诉，由客服代为录入（买家线上自助售后由系统自动建单，不走这里）。
 *  金额/描述/凭证图片上传（multipart + 幂等键）。
 */
import { useRef, useState } from "react";
import { genIdempotencyKey } from "../api/client";

export default function CreateCaseForm({ onCreated }: { onCreated: () => void }) {
  const applicantId = "user-demo";
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
    setLoading(true);
    try {
      const form = new FormData();
      form.append("applicant_id", applicantId);
      form.append("order_id", orderId);
      form.append("applicant_amount", String(Math.round(parseFloat(amount) * 100)));
      form.append("actual_amount", String(Math.round(parseFloat(amount) * 100)));
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
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        <label style={{ fontSize: 13 }}>
          订单号
          <input value={orderId} required onChange={(e) => setOrderId(e.target.value)} style={inputStyle} placeholder="order-001" />
        </label>
        <label style={{ fontSize: 13 }}>
          退款金额（元）
          <input value={amount} required type="number" min="0" step="0.01" onChange={(e) => setAmount(e.target.value)} style={inputStyle} placeholder="如 350.00" />
        </label>
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
