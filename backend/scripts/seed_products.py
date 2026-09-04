"""商品种子脚本：读 products/products.json（小米 392 条），upsert 进 Product 表。

用法：
    cd backend
    python scripts/seed_products.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal
from app.domain.models import Product


def main() -> None:
    path = Path(__file__).resolve().parents[2] / "products" / "products.json"
    data = json.loads(path.read_text(encoding="utf-8"))

    db = SessionLocal()
    try:
        added = 0
        for d in data:
            pid = int(d["product_id"])
            prod = db.query(Product).filter_by(product_id=pid).first()
            if prod is None:
                prod = Product(product_id=pid)
                db.add(prod)
                added += 1
            prod.name = d["name"]
            prod.price_cents = int(d["price_cents"])
            prod.description = d.get("description") or ""
            prod.category = d.get("category") or ""
            # 优先用远程 CDN 图，便于前端直接加载
            prod.image_url = d.get("source_url") or d.get("image") or None
            prod.is_active = True
        db.commit()
        print(f"seed done: rows={len(data)} added={added} updated={len(data) - added}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
