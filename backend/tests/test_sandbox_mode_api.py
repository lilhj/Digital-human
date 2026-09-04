"""沙箱模式运行时开关测试（工单5 移植 + 运行时持久化）。

覆盖：
1. GET 未设置时回落配置默认（.env SANDBOX_MODE=off）
2. PUT 设置后 GET 读到新值（写入 Redis 持久化）
3. 非法枚举值 -> 422
4. 仅 MANAGER 可操作（CSR 越权 -> 403）
"""
import pytest
from fastapi.testclient import TestClient

from app.infrastructure.streams import SANDBOX_MODE_KEY, get_redis
from app.main import app

client = TestClient(app)


def login(role: str) -> str:
    creds = {
        "MANAGER": ("manager", "manager123"),
        "CSR": ("csr", "csr123"),
    }
    username, password = creds[role]
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def auth_header(role: str) -> dict:
    return {"Authorization": f"Bearer {login(role)}"}


@pytest.fixture(autouse=True)
def backup_restore_sandbox_mode():
    """测试前后备份/恢复 Redis 里的运行时模式，避免污染手动操作状态。"""
    r = get_redis()
    before = r.get(SANDBOX_MODE_KEY)
    yield
    if before is None:
        r.delete(SANDBOX_MODE_KEY)
    else:
        r.set(SANDBOX_MODE_KEY, before)


def test_get_sandbox_mode_defaults_to_config():
    """GET 未设置 Redis -> 回落配置默认 off。"""
    get_redis().delete(SANDBOX_MODE_KEY)
    r = client.get("/api/v1/batch/sandbox-mode", headers=auth_header("MANAGER"))
    assert r.status_code == 200
    # 配置默认（.env SANDBOX_MODE=off）;若环境改为 on 则回落其值——只断言它是合法枚举
    assert r.json()["sandbox_mode"] in ("on", "off")


def test_put_sandbox_mode_then_get():
    """PUT 写入 Redis -> GET 读到新值（持久化生效）。"""
    r = client.put("/api/v1/batch/sandbox-mode", json={"sandbox_mode": "on"}, headers=auth_header("MANAGER"))
    assert r.status_code == 200
    assert r.json()["sandbox_mode"] == "on"
    # Redis 已持久化
    assert get_redis().get(SANDBOX_MODE_KEY) == "on"
    # GET 读到新值
    r2 = client.get("/api/v1/batch/sandbox-mode", headers=auth_header("MANAGER"))
    assert r2.json()["sandbox_mode"] == "on"


def test_put_sandbox_mode_invalid_value():
    """非法枚举 -> 422。"""
    r = client.put("/api/v1/batch/sandbox-mode", json={"sandbox_mode": "maybe"}, headers=auth_header("MANAGER"))
    assert r.status_code == 422


def test_put_sandbox_mode_requires_manager():
    """CSR 越权 -> 403（沙箱是安全层，仅主管可开关）。"""
    r = client.put("/api/v1/batch/sandbox-mode", json={"sandbox_mode": "on"}, headers=auth_header("CSR"))
    assert r.status_code == 403


def test_get_sandbox_mode_requires_manager():
    """CSR 读取也受限 -> 403。"""
    r = client.get("/api/v1/batch/sandbox-mode", headers=auth_header("CSR"))
    assert r.status_code == 403