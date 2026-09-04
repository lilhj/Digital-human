"""买家全链路冒烟测试（绕过系统代理，直连 127.0.0.1:8000）。

用法：
    cd backend
    python scripts/smoke_buyer.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

BASE = "http://127.0.0.1:8000"


def main() -> None:
    # trust_env=False 绕过 Clash 等系统代理，直连本地
    with httpx.Client(base_url=BASE, trust_env=False) as c:
        assert c.get("/api/v1/health").json()["status"] == "ok"
        print("[1] health ok")

        phone = "13800000001"
        # 注册（已存在则忽略 409）
        r = c.post("/api/v1/buyer/auth/register", json={"phone": phone, "password": "test1234", "nickname": "铁牛"})
        print(f"[2] register -> {r.status_code} {r.json().get('code','')}")
        if r.status_code == 409:
            pass
        elif r.status_code != 201:
            raise SystemExit(f"register failed: {r.text}")

        # 登录拿 BUYER token
        r = c.post("/api/v1/buyer/auth/login", json={"phone": phone, "password": "test1234"})
        assert r.status_code == 200, r.text
        token = r.json()["access_token"]
        print(f"[3] login ok, token_type={r.json().get('token_type')}")
        H = {"Authorization": f"Bearer {token}"}

        # 商品列表
        r = c.get("/api/v1/products", params={"page": 1, "page_size": 2})
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        print(f"[4] products list -> total={r.json()['total']}, got={len(items)}")
        pid = items[0]["product_id"]

        # 加购
        r = c.post("/api/v1/cart", json={"product_id": pid, "quantity": 2}, headers=H)
        assert r.status_code == 201, r.text
        print("[5] add to cart ok")

        # 购物车
        r = c.get("/api/v1/cart", headers=H)
        assert r.status_code == 200, r.text
        cart_items = r.json()["items"]
        print(f"[6] cart -> {len(cart_items)} item(s), subtotal={cart_items[0]['subtotal_cents']} 分")
        cart_item_id = cart_items[0]["id"]

        # 下单（R1：传购物车条目 ID）
        r = c.post("/api/v1/orders", json={"cart_item_ids": [cart_item_id]}, headers=H)
        assert r.status_code == 201, r.text
        order = r.json()
        print(f"[7] create order -> {order['order_no']} status={order['status']} total={order['total_cents']} 分")
        # 购物车应已清空
        assert c.get("/api/v1/cart", headers=H).json()["items"] == []
        print("    cart cleared after order")

        # 支付（D1：同步）
        r = c.post(f"/api/v1/orders/{order['id']}/pay", headers=H)
        assert r.status_code == 200, r.text
        print(f"[8] pay -> status={r.json()['status']}")

        # 订单列表 + 详情
        r = c.get("/api/v1/orders", headers=H)
        assert r.status_code == 200 and r.json()["total"] >= 1
        print(f"[9] orders list -> total={r.json()['total']}")
        r = c.get(f"/api/v1/orders/{order['id']}", headers=H)
        assert r.status_code == 200 and r.json()["items"]
        print(f"[10] order detail ok, items={len(r.json()['items'])}")

        # 买家发起售后（R2：BUYER 令牌可访问 create_case）
        r = c.post(
            "/api/v1/cases",
            data={
                "applicant_id": phone,
                "order_id": order["order_no"],
                "applicant_amount": order["total_cents"],
                "actual_amount": order["total_cents"],
                "description": "商品质量问题，申请退款",
            },
            headers=H,
        )
        print(f"[11] buyer create_case -> {r.status_code} {r.json().get('code','')} {r.json().get('ticket_no','')}")
        assert r.status_code == 202, r.text

        print("\nALL BUYER FLOW SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
