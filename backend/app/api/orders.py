"""订单接口（v2.0 §14 订单）：下单（锁总额快照）/ 模拟支付 / 列表 / 详情，必须买家登录。"""
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.schemas import CreateOrderRequest, OrderItemOut, OrderOut
from app.core.database import get_db
from app.core.security import get_current_customer
from app.domain.models import CartItem, Customer, Order, OrderItem, OrderStatus, Product

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])


def _gen_order_no() -> str:
    return f"O{datetime.now():%Y%m%d%H%M%S}{uuid.uuid4().hex[:6].upper()}"


def _serialize(order: Order) -> dict:
    return OrderOut(
        id=order.id,
        order_no=order.order_no,
        total_cents=order.total_cents,
        status=order.status,
        created_at=order.created_at,
        paid_at=order.paid_at,
        refunded_at=order.refunded_at,
        items=[
            OrderItemOut(
                product_id=oi.product_id,
                product_name=oi.product_name,
                price_cents=oi.price_cents,
                quantity=oi.quantity,
                subtotal_cents=oi.subtotal_cents,
            )
            for oi in order.items
        ],
    ).model_dump(mode="json")


@router.post("", response_model=OrderOut, status_code=201)
def create_order(
    body: CreateOrderRequest,
    customer: Annotated[Customer, Depends(get_current_customer)],
    db: Annotated[Session, Depends(get_db)],
):
    # R1：以购物车为真相，防前端篡改金额
    items = (
        db.query(CartItem)
        .filter(CartItem.id.in_(body.cart_item_ids), CartItem.customer_id == customer.id)
        .all()
    )
    if not items or len(items) != len(set(body.cart_item_ids)):
        raise HTTPException(
            status_code=400,
            detail={"code": "CART_MISMATCH", "message": "部分购物车条目不存在或不属于当前买家"},
        )

    order = Order(
        order_no=_gen_order_no(),
        customer_id=customer.id,
        total_cents=0,
        status=OrderStatus.PENDING_PAYMENT.value,
    )
    db.add(order)
    db.flush()

    total = 0
    for ci in items:
        product: Product = ci.product
        subtotal = product.price_cents * ci.quantity
        total += subtotal
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=str(product.product_id),
                product_name=product.name,
                price_cents=product.price_cents,
                quantity=ci.quantity,
                subtotal_cents=subtotal,
            )
        )

    order.total_cents = total
    # 下单后清空对应购物车条目
    for ci in items:
        db.delete(ci)
    db.commit()
    db.refresh(order)
    return _serialize(order)


@router.post("/{order_id}/pay", response_model=OrderOut)
def pay_order(
    order_id: int,
    customer: Annotated[Customer, Depends(get_current_customer)],
    db: Annotated[Session, Depends(get_db)],
):
    order = db.query(Order).filter_by(id=order_id, customer_id=customer.id).first()
    if order is None:
        raise HTTPException(
            status_code=404, detail={"code": "ORDER_NOT_FOUND", "message": "订单不存在"}
        )
    if order.status != OrderStatus.PENDING_PAYMENT.value:
        raise HTTPException(
            status_code=409,
            detail={"code": "INVALID_STATE", "message": f"当前状态 {order.status} 不可支付"},
        )
    # D1：同步支付，零延迟同事务（MVP 模拟支付）
    order.status = OrderStatus.PAID.value
    order.paid_at = datetime.now()
    db.commit()
    db.refresh(order)
    return _serialize(order)


@router.get("")
def list_orders(
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    customer: Annotated[Customer, Depends(get_current_customer)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
):
    q = db.query(Order).filter_by(customer_id=customer.id)
    if status:
        q = q.filter_by(status=status)
    total = q.count()
    orders = (
        q.order_by(Order.created_at.desc())
        .offset(max(page - 1, 0) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_serialize(o) for o in orders],
    }


@router.get("/{order_id}", response_model=OrderOut)
def get_order(
    order_id: int,
    customer: Annotated[Customer, Depends(get_current_customer)],
    db: Annotated[Session, Depends(get_db)],
):
    order = db.query(Order).filter_by(id=order_id, customer_id=customer.id).first()
    if order is None:
        raise HTTPException(
            status_code=404, detail={"code": "ORDER_NOT_FOUND", "message": "订单不存在"}
        )
    return _serialize(order)
