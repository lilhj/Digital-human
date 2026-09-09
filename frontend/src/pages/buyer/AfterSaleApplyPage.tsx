/** 申请售后：三卡片（退款 / 退货退款 / 换货）+ 原因 + 金额 + 凭证上传（工单1：凭证入 CaseEvidence 走 OCR 识别）。
 *  提交 = 系统自动建单（v2.0 核心规则：买家自助提交即 create_case，无需客服录入）。
 */
import { useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { getBuyer } from "../../api/client";
import BuyerChatWidget from "../../components/BuyerChatWidget";
import { createAfterSale, getOrder, type AfterSale } from "../../api/mockBuyer";
import { btnPrimary, card, fen, input } from "../../theme";

const TYPES: { type: AfterSale["type"]; icon: string; desc: string }[] = [
  { type: "退款", icon: "💰", desc: "未收到货 / 快递丢件，仅退钱" },
  { type: "退货退款", icon: "📦", desc: "已收到货，寄回商品并退款" },
  { type: "换货", icon: "🔄", desc: "商品问题，换一件新的（转人工处理）" },
];

const MAX_EVIDENCE_SIZE = 2 * 1024 * 1024; // 2MB（mock 存 localStorage，后端落地后走 multipart 无此限）

export default function AfterSaleApplyPage() {
  const { id: orderId } = useParams();
  const buyer = getBuyer();
  const navigate = useNavigate();
  // 工单8 交叉校验：售后类型无默认选中，用户必须显式三选一（避免默认「退款」吞掉换货诉求）
  const [type, setType] = useState<AfterSale["type"] | null>(null);
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [evidence, setEvidence] = useState<string | null>(null);
  const [evidenceFile, setEvidenceFile] = useState<File | null>(null);
  const [evidenceName, setEvidenceName] = useState("");
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  if (!buyer) {
    return (
      <p style={{ padding: 24 }}>
        请先 <Link to="/buyer/login" style={{ color: "#0d6efd" }}>登录</Link>。
      </p>
    );
  }

  const order = getOrder(buyer.phone, orderId ?? "");
  if (!order) {
    return (
      <p style={{ padding: 24 }}>
        订单不存在。<Link to="/buyer/orders" style={{ color: "#0d6efd" }}>返回我的订单</Link>
      </p>
    );
  }

  const maxAmount = (order.total / 100).toFixed(2);

  function pickEvidence(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    setError("");
    if (!file) return;
    if (file.size > MAX_EVIDENCE_SIZE) {
      setError("凭证图片不能超过 2MB（演示环境限制）");
      if (fileRef.current) fileRef.current.value = "";
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      setEvidence(reader.result as string);
      setEvidenceName(file.name);
    };
    reader.readAsDataURL(file);
    setEvidenceFile(file); // 保留原始 File，随 multipart 上传到后端（此前凭证从未实际发出）
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    // 显式三选一：未选择售后类型禁止提交（消除默认值导致的意图误判）
    if (!type) {
      setError("请先选择售后类型（退款 / 退货退款 / 换货）");
      return;
    }
    if (type !== "换货") {
      const a = parseFloat(amount);
      if (!a || a <= 0 || a > order!.total / 100) {
        setError(`退款金额需在 0 ~ ${maxAmount} 元之间`);
        return;
      }
    }
    if (!reason.trim()) {
      setError("请填写申请原因");
      return;
    }
    const finalAmount = type === "换货" ? 0 : Math.round(parseFloat(amount) * 100);
    try {
      await createAfterSale(buyer!.phone, order!.id, type, finalAmount, reason, evidence, evidenceFile);
      navigate("/buyer/aftersale?created=1");
    } catch (e) {
      // 后端 4xx 统一抛出（如 409 DUPLICATE_CASE 重复申请、403 越权、422 金额超限），如实展示给买家
      setError(e instanceof Error ? e.message : "提交失败，请稍后重试");
    }
  }

  return (
    <div style={{ maxWidth: 640, margin: "0 auto" }}>
      <Link to="/buyer/orders" style={{ fontSize: 13, color: "#0d6efd" }}>← 返回我的订单</Link>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
        <h1 style={{ fontSize: 20, margin: "12px 0 4px" }}>申请售后</h1>
        {/* 售后页入口：带订单上下文的智能客服（如咨询「这单能不能退」「退款多久到账」） */}
        <BuyerChatWidget
          orderId={Number(order.id)}
          floating={false}
          trigger={
            <span
              style={{
                fontSize: 12,
                color: "#0d6efd",
                border: "1px dashed #0d6efd",
                padding: "3px 10px",
                borderRadius: 12,
                cursor: "pointer",
              }}
            >
              💬 智能客服
            </span>
          }
        />
      </div>
      <p style={{ fontSize: 13, color: "#777", margin: "0 0 16px" }}>
        订单 {order.id} · 实付 {fen(order.total)} · 提交后系统自动建单，无需等待客服录入
      </p>

      <form onSubmit={submit}>
        {/* 三卡片 */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10, marginBottom: 16 }}>
          {TYPES.map((t) => (
            <div
              key={t.type}
              onClick={() => setType(t.type)}
              style={{
                ...card,
                cursor: "pointer",
                textAlign: "center",
                border: type === t.type ? "2px solid #0d6efd" : "1px solid #eee",
                background: type === t.type ? "#e7f1ff" : "#fff",
              }}
            >
              <div style={{ fontSize: 28 }}>{t.icon}</div>
              <div style={{ fontSize: 15, fontWeight: 700, margin: "6px 0" }}>{t.type}</div>
              <div style={{ fontSize: 12, color: "#777" }}>{t.desc}</div>
            </div>
          ))}
        </div>

        <section style={{ ...card, marginBottom: 14 }}>
          {type !== null && type !== "换货" && (
            <label style={{ fontSize: 13, display: "block", marginBottom: 12 }}>
              退款金额（元，上限 {maxAmount}）
              <input
                type="number"
                min="0.01"
                max={maxAmount}
                step="0.01"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                style={input}
                placeholder={maxAmount}
              />
            </label>
          )}
          <label style={{ fontSize: 13, display: "block" }}>
            申请原因
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              style={{ ...input, width: "100%" }}
              placeholder={type === "退款" ? "如：快递丢件，未收到货" : type === "退货退款" ? "如：商品破损，无法正常使用" : "如：尺码不合适，换大一码"}
            />
          </label>

          {/* 凭证上传（工单1：凭证图片 → CaseEvidence → Evidence OCR 节点识别） */}
          <div style={{ marginTop: 14 }}>
            <span style={{ fontSize: 13 }}>上传凭证（选填，破损照片 / 快递单 / 发票，随工单进入 OCR 识别）</span>
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 8 }}>
              <label style={{ cursor: "pointer", padding: "7px 16px", border: "1px dashed #0d6efd", color: "#0d6efd", borderRadius: 6, fontSize: 13 }}>
                选择图片
                <input ref={fileRef} type="file" accept="image/*" style={{ display: "none" }} onChange={pickEvidence} />
              </label>
              {evidenceName && (
                <span style={{ fontSize: 12, color: "#777" }}>
                  {evidenceName}
                  <button
                    type="button"
                    onClick={() => { setEvidence(null); setEvidenceName(""); if (fileRef.current) fileRef.current.value = ""; }}
                    style={{ marginLeft: 8, border: "none", background: "none", color: "#dc3545", cursor: "pointer", fontSize: 12 }}
                  >
                    移除
                  </button>
                </span>
              )}
            </div>
            {evidence && (
              <div style={{ marginTop: 10, border: "1px solid #eee", borderRadius: 8, padding: 8, display: "inline-block", background: "#fafafa" }}>
                <img src={evidence} alt="凭证预览" style={{ maxWidth: 220, maxHeight: 160, objectFit: "contain", display: "block" }} />
              </div>
            )}
          </div>
        </section>

        <button type="submit" style={{ ...btnPrimary, width: "100%", padding: 12, fontSize: 16 }}>
          提交申请（自动建单）
        </button>
        {error && <p style={{ color: "#dc3545", fontSize: 13, marginTop: 10 }}>{error}</p>}
      </form>
    </div>
  );
}
