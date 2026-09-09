"""ADMIN 用户管理接口单测（phase12）：越权 403 / 自操作 400 / 密码重置后旧密码失效 / 买家停用拦截登录。

安全约束验证：
1. 未带 token -> 401；客服/主管 -> 403（仅 ADMIN 可用）；
2. 员工与买家列表响应绝不含 password_hash（密码哈希不可逆查看）；
3. 新增员工后可登录；重名 -> 409；停用员工后无法登录（auth.login 过滤 is_active）；
4. 管理员禁止修改/停用自己（防锁死门外 / 降权自杀）-> 400 SELF_OPERATION；
5. 重置密码后旧密码失效、新密码可登录；
6. 买家停用后登录被拦（403 ACCOUNT_DISABLED），重新启用后恢复。
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.domain.models import Customer, User
from app.main import app

client = TestClient(app)

MARKER = f"au-{uuid.uuid4().hex[:8]}"
PWD = "TestPass123"


def _staff_headers(role: str) -> dict:
    creds = {"manager": ("manager", "manager123"), "csr": ("csr", "csr123"), "admin": ("admin", "admin123")}
    u, p = creds[role]
    r = client.post("/api/v1/auth/login", json={"username": u, "password": p})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(autouse=True)
def cleanup():
    yield
    db = SessionLocal()
    try:
        db.query(User).filter(User.username.like(f"{MARKER}%")).delete(synchronize_session=False)
        db.query(Customer).filter(Customer.phone.like(f"{MARKER}%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


# ---------- 权限边界 ----------


class TestPermission:
    def test_no_token_401(self):
        assert client.get("/api/v1/admin/users").status_code == 401

    def test_csr_forbidden_403(self):
        r = client.get("/api/v1/admin/users", headers=_staff_headers("csr"))
        assert r.status_code == 403
        assert r.json()["code"] == "FORBIDDEN"

    def test_manager_forbidden_403(self):
        r = client.get("/api/v1/admin/users", headers=_staff_headers("manager"))
        assert r.status_code == 403

    def test_csr_cannot_create_user_403(self):
        r = client.post(
            "/api/v1/admin/users",
            json={"username": f"{MARKER}-noperm", "password": PWD, "role": "CSR", "display_name": "无权限"},
            headers=_staff_headers("csr"),
        )
        assert r.status_code == 403


# ---------- 员工管理 ----------


class TestUsers:
    def test_list_users_no_password_hash(self):
        r = client.get("/api/v1/admin/users", headers=_staff_headers("admin"))
        assert r.status_code == 200
        data = r.json()
        assert any(u["username"] == "admin" for u in data)
        assert all("password_hash" not in u for u in data)
        assert all("created_at" in u and "role" in u and "is_active" in u for u in data)

    def test_create_user_then_login(self):
        username = f"{MARKER}-u1"
        r = client.post(
            "/api/v1/admin/users",
            json={"username": username, "password": PWD, "role": "CSR", "display_name": "测试客服"},
            headers=_staff_headers("admin"),
        )
        assert r.status_code == 201
        assert "password_hash" not in r.json()
        # 新账号能正常登录
        r = client.post("/api/v1/auth/login", json={"username": username, "password": PWD})
        assert r.status_code == 200
        assert r.json()["role"] == "CSR"

    def test_create_duplicate_username_409(self):
        username = f"{MARKER}-dup"
        body = {"username": username, "password": PWD, "role": "CSR", "display_name": "重名"}
        assert client.post("/api/v1/admin/users", json=body, headers=_staff_headers("admin")).status_code == 201
        r = client.post("/api/v1/admin/users", json=body, headers=_staff_headers("admin"))
        assert r.status_code == 409
        assert r.json()["code"] == "USERNAME_TAKEN"

    def test_create_invalid_role_422(self):
        r = client.post(
            "/api/v1/admin/users",
            json={"username": f"{MARKER}-badrole", "password": PWD, "role": "BOSS", "display_name": "非法角色"},
            headers=_staff_headers("admin"),
        )
        assert r.status_code == 422

    def test_self_update_forbidden_400(self):
        admin_id = next(
            u["id"] for u in client.get("/api/v1/admin/users", headers=_staff_headers("admin")).json() if u["username"] == "admin"
        )
        # 停用自己 -> 400
        r = client.patch(
            f"/api/v1/admin/users/{admin_id}", json={"is_active": False}, headers=_staff_headers("admin")
        )
        assert r.status_code == 400
        assert r.json()["code"] == "SELF_OPERATION"
        # 降权自己 -> 400
        r = client.patch(
            f"/api/v1/admin/users/{admin_id}", json={"role": "CSR"}, headers=_staff_headers("admin")
        )
        assert r.status_code == 400

    def test_patch_role_and_deactivate_blocks_login(self):
        username = f"{MARKER}-u2"
        created = client.post(
            "/api/v1/admin/users",
            json={"username": username, "password": PWD, "role": "CSR", "display_name": "改角色"},
            headers=_staff_headers("admin"),
        ).json()
        # 改角色
        r = client.patch(
            f"/api/v1/admin/users/{created['id']}", json={"role": "MANAGER"}, headers=_staff_headers("admin")
        )
        assert r.status_code == 200
        # 停用后无法登录（auth.login 过滤 is_active）
        client.patch(
            f"/api/v1/admin/users/{created['id']}", json={"is_active": False}, headers=_staff_headers("admin")
        )
        assert client.post("/api/v1/auth/login", json={"username": username, "password": PWD}).status_code == 401
        # 重新启用恢复登录
        client.patch(
            f"/api/v1/admin/users/{created['id']}", json={"is_active": True}, headers=_staff_headers("admin")
        )
        assert client.post("/api/v1/auth/login", json={"username": username, "password": PWD}).status_code == 200

    def test_reset_password_old_invalid_new_valid(self):
        username = f"{MARKER}-u3"
        created = client.post(
            "/api/v1/admin/users",
            json={"username": username, "password": PWD, "role": "CSR", "display_name": "重置"},
            headers=_staff_headers("admin"),
        ).json()
        r = client.post(
            f"/api/v1/admin/users/{created['id']}/reset-password",
            json={"new_password": "NewPass456"},
            headers=_staff_headers("admin"),
        )
        assert r.status_code == 200
        # 旧密码失效
        assert client.post("/api/v1/auth/login", json={"username": username, "password": PWD}).status_code == 401
        # 新密码可登录
        assert client.post("/api/v1/auth/login", json={"username": username, "password": "NewPass456"}).status_code == 200


# ---------- 买家管理 ----------


def _register_buyer(phone: str, pwd: str = "buyer123456"):
    r = client.post(
        "/api/v1/buyer/auth/register",
        json={"phone": phone, "password": pwd, "nickname": "买家测试"},
    )
    assert r.status_code in (200, 201), r.text
    return phone


class TestCustomers:
    def test_list_customers_no_password_hash(self):
        r = client.get("/api/v1/admin/customers", headers=_staff_headers("admin"))
        assert r.status_code == 200
        assert all("password_hash" not in c for c in r.json())
        assert all("phone" in c and "is_active" in c for c in r.json())

    def test_reset_customer_password_old_invalid(self):
        phone = f"{MARKER}20001"
        _register_buyer(phone)
        cid = next(
            c["id"] for c in client.get("/api/v1/admin/customers", headers=_staff_headers("admin")).json() if c["phone"] == phone
        )
        r = client.post(
            f"/api/v1/admin/customers/{cid}/reset-password",
            json={"new_password": "NewBuyer789"},
            headers=_staff_headers("admin"),
        )
        assert r.status_code == 200
        # 旧密码失效、新密码可登录
        assert client.post("/api/v1/buyer/auth/login", json={"phone": phone, "password": "buyer123456"}).status_code == 401
        assert client.post("/api/v1/buyer/auth/login", json={"phone": phone, "password": "NewBuyer789"}).status_code == 200

    def test_toggle_active_blocks_login(self):
        phone = f"{MARKER}20002"
        _register_buyer(phone)
        cid = next(
            c["id"] for c in client.get("/api/v1/admin/customers", headers=_staff_headers("admin")).json() if c["phone"] == phone
        )
        # 停用 -> 登录 403 ACCOUNT_DISABLED
        r = client.post(f"/api/v1/admin/customers/{cid}/toggle-active", json={}, headers=_staff_headers("admin"))
        assert r.status_code == 200
        r = client.post("/api/v1/buyer/auth/login", json={"phone": phone, "password": "buyer123456"})
        assert r.status_code == 403
        assert r.json()["code"] == "ACCOUNT_DISABLED"
        # 重新启用 -> 恢复登录
        client.post(f"/api/v1/admin/customers/{cid}/toggle-active", json={}, headers=_staff_headers("admin"))
        assert client.post("/api/v1/buyer/auth/login", json={"phone": phone, "password": "buyer123456"}).status_code == 200
