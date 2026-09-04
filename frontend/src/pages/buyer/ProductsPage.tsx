/** 商品列表：分类筛选 + 关键词搜索 + 分页（392 个小米真实商品）。 */
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { PRODUCTS } from "../../api/mockBuyer";
import { btnGhost, card, fen, input } from "../../theme";

const CATEGORIES = ["全部", ...new Set(PRODUCTS.map((p) => p.category))];
const PAGE_SIZE = 24;

export default function ProductsPage() {
  const [cat, setCat] = useState("全部");
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);

  const filtered = useMemo(() => {
    const kw = keyword.trim();
    return PRODUCTS.filter(
      (p) =>
        (cat === "全部" || p.category === cat) &&
        (kw === "" || p.name.toLowerCase().includes(kw.toLowerCase()) || p.desc.includes(kw)),
    );
  }, [cat, keyword]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const current = Math.min(page, totalPages);
  const list = filtered.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE);

  function selectCat(c: string) {
    setCat(c);
    setPage(1);
  }

  return (
    <div>
      {/* 搜索 + 分类 */}
      <div style={{ display: "flex", gap: 10, marginBottom: 12 }}>
        <input
          value={keyword}
          onChange={(e) => { setKeyword(e.target.value); setPage(1); }}
          placeholder="搜索商品名 / 描述（如 Xiaomi 17、空调）"
          style={{ ...input, marginTop: 0, maxWidth: 360 }}
        />
        <span style={{ fontSize: 12, color: "#999", alignSelf: "center" }}>共 {filtered.length} 件</span>
      </div>
      <div style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
        {CATEGORIES.map((c) => (
          <button
            key={c}
            onClick={() => selectCat(c)}
            style={{
              padding: "6px 14px",
              borderRadius: 16,
              border: cat === c ? "2px solid #0d6efd" : "1px solid #ccc",
              background: cat === c ? "#e7f1ff" : "#fff",
              cursor: "pointer",
              fontSize: 13,
            }}
          >
            {c}
          </button>
        ))}
      </div>

      {/* 商品网格 */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 14 }}>
        {list.map((p) => (
          <Link key={p.id} to={`/buyer/product/${p.id}`} style={{ textDecoration: "none", color: "inherit" }}>
            <div style={{ ...card, padding: 0, overflow: "hidden", height: "100%" }}>
              <div style={{ height: 150, background: "#fff", display: "flex", alignItems: "center", justifyContent: "center", borderBottom: "1px solid #f0f0f0" }}>
                <img src={p.image} alt={p.name} loading="lazy" style={{ maxWidth: "85%", maxHeight: "85%", objectFit: "contain" }} />
              </div>
              <div style={{ padding: 12 }}>
                <div style={{ fontSize: 14, fontWeight: 600, minHeight: 38 }}>{p.name}</div>
                <div style={{ fontSize: 12, color: "#999", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.desc.split("\n")[0]}</div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 8 }}>
                  <span style={{ color: "#dc3545", fontWeight: 700, fontSize: 16 }}>{fen(p.price)}</span>
                  <span style={{ fontSize: 12, color: "#999" }}>库存 {p.stock}</span>
                </div>
              </div>
            </div>
          </Link>
        ))}
      </div>

      {/* 分页 */}
      {totalPages > 1 && (
        <div style={{ display: "flex", justifyContent: "center", alignItems: "center", gap: 12, marginTop: 20 }}>
          <button onClick={() => setPage(current - 1)} disabled={current <= 1} style={btnGhost}>上一页</button>
          <span style={{ fontSize: 13, color: "#555" }}>{current} / {totalPages}</span>
          <button onClick={() => setPage(current + 1)} disabled={current >= totalPages} style={btnGhost}>下一页</button>
        </div>
      )}
    </div>
  );
}
