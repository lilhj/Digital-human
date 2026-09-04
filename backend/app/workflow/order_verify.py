"""订单三查校验（v2.0 §7/§8）：订单真实性 / 商品一致性 / 价格一致性 + 防重复退款。

落点：决策流最前面（intake 之后、evidence 之前）作为硬闸，堵住
「伪造订单 / 偷换商品 / 虚报金额 / 重复退款」四类资损漏洞。

- order_exists：Order 表按 order_no 查不到 → REJECT（伪造订单）
- already_refunded：订单已处 REFUNDING/REFUNDED/EXCHANGING/CLOSED → REJECT（重复退款）
- product_matched：订单明细缺失或快照残缺 → REVIEW（转人工）
- price_matched：申报额>实付额 / 买家申报实付≠真实总额 / Σ明细≠总额 → REVIEW（转人工）
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.domain.models import Customer, Order, OrderItem, OrderStatus


# 已售后订单状态（任一命中即视为不可重复退款）
_ALREADY_REFUNDED = {
    OrderStatus.REFUNDING.value,
    OrderStatus.REFUNDED.value,
    OrderStatus.EXCHANGING.value,
    OrderStatus.CLOSED.value,
}


@dataclass
class OrderVerifyResult:
    """三查 + 防重复结果；result 直接决定路由。"""

    order_exists: bool
    product_matched: bool
    price_matched: bool
    already_refunded: bool
    reason: str
    # PASS -> 走正常链路；REJECT -> 直接拒绝（伪造/重复）；REVIEW -> 转人工（不一致）
    result: str


class OrderVerifyProvider:
    """订单三查接口（可替换为 Fake / DB 实现）。"""

    def verify(
        self, *, order_id: str, applicant_amount: int, actual_amount: int,
        applicant_id: str | None = None,
    ) -> OrderVerifyResult:
        raise NotImplementedError


class FakeOrderVerifyProvider(OrderVerifyProvider):
    """开发/测试默认：一律放行（不依赖真实订单数据）。"""

    def verify(
        self, *, order_id: str, applicant_amount: int, actual_amount: int,
        applicant_id: str | None = None,
    ) -> OrderVerifyResult:
        return OrderVerifyResult(
            order_exists=True,
            product_matched=True,
            price_matched=True,
            already_refunded=False,
            reason="Fake 放行（未配置真实订单校验）",
            result="PASS",
        )


class DbOrderVerifyProvider(OrderVerifyProvider):
    """生产实现：直查 Order / OrderItem 表执行三查（资损红线，必须开启）。"""

    def verify(
        self, *, order_id: str, applicant_amount: int, actual_amount: int,
        applicant_id: str | None = None,
    ) -> OrderVerifyResult:
        db: Session = SessionLocal()
        try:
            order = db.query(Order).filter_by(order_no=order_id).first()
            # 兼容入参为 Order 主键 id 的情况（前端 mapOrder 曾把后端自增 id 直接当 order_id 传）
            if order is None and order_id.strip().isdigit():
                order = db.get(Order, int(order_id))
            if order is None:
                return OrderVerifyResult(
                    order_exists=False,
                    product_matched=False,
                    price_matched=False,
                    already_refunded=False,
                    reason="订单不存在（疑似伪造订单号）",
                    result="REJECT",
                )

            # 归属校验（M-1 越权防护，纵深防御）：订单必须属于申请人名下。
            # applicant_id 为手机号；查 Customer 表核对归属。找不到申请人（兜底/测试）不阻断，
            # 交给 API 层身份校验；找得到但已就订单属于他人 → 直接拒（越权退款）。
            if applicant_id:
                customer = db.query(Customer).filter_by(phone=applicant_id).first()
                if customer is not None and order.customer_id != customer.id:
                    return OrderVerifyResult(
                        order_exists=True,
                        product_matched=True,
                        price_matched=False,
                        already_refunded=False,
                        reason="订单不属于申请人，疑似越权退款",
                        result="REJECT",
                    )

            # 防重复退款：已售后订单二次申请直接拒
            if order.status in _ALREADY_REFUNDED:
                return OrderVerifyResult(
                    order_exists=True,
                    product_matched=True,
                    price_matched=True,
                    already_refunded=True,
                    reason=f"订单已处于 {order.status} 状态，不可重复退款",
                    result="REJECT",
                )

            items: list[OrderItem] = order.items
            # 商品一致性：明细必须存在且快照完整（名称/价格非空）
            product_ok = bool(items) and all(
                it.product_name and it.price_cents is not None for it in items
            )
            # 价格一致性三层对账
            # ① 申报额 ≤ 真实实付额
            # ② 买家申报实付额 == 真实订单总额
            # ③ Σ(明细小计) == 订单总额
            sum_subtotal = sum(it.subtotal_cents for it in items)
            price_ok = (
                applicant_amount <= order.total_cents
                and actual_amount == order.total_cents
                and sum_subtotal == order.total_cents
            )

            if not product_ok:
                return OrderVerifyResult(
                    order_exists=True,
                    product_matched=False,
                    price_matched=price_ok,
                    already_refunded=False,
                    reason="订单明细缺失或快照不完整，转人工复核",
                    result="REVIEW",
                )
            if not price_ok:
                return OrderVerifyResult(
                    order_exists=True,
                    product_matched=True,
                    price_matched=False,
                    already_refunded=False,
                    reason="价格对账不平（申报额>实付额 / 实付额≠订单总额 / 明细合计≠总额），转人工复核",
                    result="REVIEW",
                )

            return OrderVerifyResult(
                order_exists=True,
                product_matched=True,
                price_matched=True,
                already_refunded=False,
                reason="三查通过",
                result="PASS",
            )
        finally:
            db.close()
