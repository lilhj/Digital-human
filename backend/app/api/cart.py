"""购物车接口（v2.0 §14 购物车）：增删改查，必须买家登录。"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.schemas import CartAddRequest, CartUpdateRequest
from app.core.database import get_db
from app.core.security import get_current_customer
from app.domain.models import CartItem, Customer, Product

router = APIRouter(prefix="/api/v1/cart", tags=["cart"])


def _serialize(ci: CartItem) -> dict:
    p = ci.product
    return {
        "id": ci.id,
        "product_id": p.product_id,
        "name": p.name,
        "price_cents": p.price_cents,
        "image_url": p.image_url,
        "quantity": ci.quantity,
        "subtotal_cents": p.price_cents * ci.quantity,
    }


@router.get("")
def get_cart(
    customer: Annotated[Customer, Depends(get_current_customer)],
    db: Annotated[Session, Depends(get_db)],
):
    items = (
        db.query(CartItem)
        .filter_by(customer_id=customer.id)
        .order_by(CartItem.id)
        .all()
    )
    return {"items": [_serialize(ci) for ci in items]}


@router.post("", status_code=201)
def add_to_cart(
    body: CartAddRequest,
    customer: Annotated[Customer, Depends(get_current_customer)],
    db: Annotated[Session, Depends(get_db)],
):
    product = db.query(Product).filter_by(product_id=body.product_id, is_active=True).first()
    if product is None:
        raise HTTPException(
            status_code=404, detail={"code": "PRODUCT_NOT_FOUND", "message": "商品不存在"}
        )
    existing = (
        db.query(CartItem)
        .filter_by(customer_id=customer.id, product_id=product.id)
        .first()
    )
    if existing is not None:
        existing.quantity += body.quantity
    else:
        db.add(
            CartItem(
                customer_id=customer.id,
                product_id=product.id,
                quantity=body.quantity,
            )
        )
    db.commit()
    return {"message": "ok"}


@router.patch("/{item_id}")
def update_cart(
    item_id: int,
    body: CartUpdateRequest,
    customer: Annotated[Customer, Depends(get_current_customer)],
    db: Annotated[Session, Depends(get_db)],
):
    item = db.query(CartItem).filter_by(id=item_id, customer_id=customer.id).first()
    if item is None:
        raise HTTPException(
            status_code=404, detail={"code": "CART_ITEM_NOT_FOUND", "message": "购物车条目不存在"}
        )
    item.quantity = body.quantity
    db.commit()
    return {"message": "ok"}


@router.delete("/{item_id}")
def delete_cart(
    item_id: int,
    customer: Annotated[Customer, Depends(get_current_customer)],
    db: Annotated[Session, Depends(get_db)],
):
    item = db.query(CartItem).filter_by(id=item_id, customer_id=customer.id).first()
    if item is None:
        raise HTTPException(
            status_code=404, detail={"code": "CART_ITEM_NOT_FOUND", "message": "购物车条目不存在"}
        )
    db.delete(item)
    db.commit()
    return {"message": "ok"}
