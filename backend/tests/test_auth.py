"""认证测试：登录、错误凭证、JWT 过期、未授权访问。"""
import jwt

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app

client = TestClient(app)


class TestLogin:
    def test_login_success(self):
        r = client.post("/api/v1/auth/login", json={"username": "manager", "password": "manager123"})
        assert r.status_code == 200
        data = r.json()
        assert data["token_type"] == "bearer"
        assert data["role"] == "MANAGER"
        assert "access_token" in data

    def test_login_wrong_password(self):
        r = client.post("/api/v1/auth/login", json={"username": "manager", "password": "wrong"})
        assert r.status_code == 401
        assert r.json()["code"] == "BAD_CREDENTIALS"

    def test_login_unknown_user(self):
        r = client.post("/api/v1/auth/login", json={"username": "nobody", "password": "x"})
        assert r.status_code == 401

    def test_login_missing_fields(self):
        r = client.post("/api/v1/auth/login", json={"username": "manager"})
        assert r.status_code == 422


class TestAuthGuard:
    def test_no_token_returns_401(self):
        r = client.get("/api/v1/cases/1")
        assert r.status_code == 401

    def test_expired_token_returns_401(self):
        settings = get_settings()
        expired = jwt.encode(
            {"sub": "manager", "role": "MANAGER", "exp": 0},
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        r = client.get(
            "/api/v1/cases/1", headers={"Authorization": f"Bearer {expired}"}
        )
        assert r.status_code == 401
        assert r.json()["code"] == "TOKEN_EXPIRED"

    def test_garbage_token_returns_401(self):
        r = client.get("/api/v1/cases/1", headers={"Authorization": "Bearer not-a-jwt"})
        assert r.status_code == 401
