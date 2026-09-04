"""商品接口（v2.0 §14 商品）：列表（分页 + 关键词搜索）/ 详情，公开访问。"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.domain.models import Product

router = APIRouter(prefix="/api/v1/products", tags=["products"])


def _serialize(p: Product) -> dict:
    return {
        "id": p.id,
        "product_id": p.product_id,
        "name": p.name,
        "price_cents": p.price_cents,
        "description": p.description,
        "category": p.category,
        "image_url": p.image_url,
        "is_active": p.is_active,
    }


@router.get("")
def list_products(
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    db: Annotated[Session, Depends(get_db)] = None,
):
    q = db.query(Product).filter_by(is_active=True)
    if keyword:
        q = q.filter(Product.name.ilike(f"%{keyword}%"))
    total = q.count()
    items = (
        q.order_by(Product.id)
        .offset(max(page - 1, 0) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_serialize(p) for p in items],
    }


@router.get("/{product_id}")
def get_product(product_id: int, db: Annotated[Session, Depends(get_db)]):
    p = db.query(Product).filter_by(product_id=product_id, is_active=True).first()
    if p is None:
        raise HTTPException(
            status_code=404, detail={"code": "PRODUCT_NOT_FOUND", "message": "商品不存在"}
        )
    return _serialize(p)
