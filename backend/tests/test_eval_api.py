"""评测报告接口测试：GET 读报告 / POST 运行（MANAGER 限定）+ EVAL_REPORT_DIR 隔离。

覆盖：
1. 未运行过 -> GET 404 EVAL_REPORT_NOT_FOUND（前端据此引导先跑评测）
2. POST mock 运行 -> 200 完整三维聚合 + 10 用例全匹配，并落盘；随后 GET 可读且带 generated_at
3. real 模式触发与 mock 分文件落盘
4. CSR 越权 POST -> 403；GET 对 CSR 只读可见
"""
import json
import pytest

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

USERS = {"manager": ("manager", "manager123"), "csr": ("csr", "csr123")}


@pytest.fixture(autouse=True)
def isolated_eval_dir(tmp_path, monkeypatch):
    """隔离评测产出目录：不污染 docs/，也不读到真实旧报告。"""
    monkeypatch.setenv("EVAL_REPORT_DIR", str(tmp_path))
    return tmp_path


def _token(role: str) -> str:
    u, p = USERS[role]
    r = client.post("/api/v1/auth/login", json={"username": u, "password": p})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _headers(role: str) -> dict:
    return {"Authorization": f"Bearer {_token(role)}"}


def test_get_report_404_when_not_run():
    r = client.get("/api/v1/eval/report", headers=_headers("manager"))
    assert r.status_code == 404
    assert r.json()["code"] == "EVAL_REPORT_NOT_FOUND"


def test_post_run_mock_and_read_back(isolated_eval_dir):
    r = client.post(
        "/api/v1/eval/run",
        json={"real": False},
        headers=_headers("manager"),
    )
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["mode"] == "mock"
    assert report["aggregate"]["count"] == 10
    assert len(report["cases"]) == 10
    # Golden Dataset 决策边界：10 例策略全命中期望
    assert all(c["match"] for c in report["cases"])
    assert report["generated_at"]

    # 落盘文件存在且可读
    f = isolated_eval_dir / "eval_report.json"
    assert f.exists()
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["mode"] == "mock"

    # GET 读回同一份报告
    g = client.get("/api/v1/eval/report", headers=_headers("manager"))
    assert g.status_code == 200
    assert g.json()["aggregate"]["count"] == 10


def test_post_run_real_writes_separate_file(isolated_eval_dir, monkeypatch):
    """real 模式（注入假裁判工厂避免真实 LLM 调用即可跑通落盘逻辑）。"""
    import app.eval.api as eval_api
    import app.eval.report as report_mod
    from app.core.config import get_settings
    from app.eval.harness import mock_judge

    # API 层对 real 模式有 LLM_API_KEY 前置校验，测试环境补假 key 放行
    monkeypatch.setattr(get_settings(), "llm_api_key", "test-key")

    async def fake_judge(case, agent_result, expected):  # noqa: ANN001
        return mock_judge(case, {"decision": agent_result, "reason": ""})

    original = eval_api.run_benchmark

    async def patched_run_benchmark(real=False, judge_factory=None):
        return await original(real=real, judge_factory=fake_judge)

    eval_api.run_benchmark = patched_run_benchmark  # api 模块内引用此符号，运行时生效
    try:
        r = client.post("/api/v1/eval/run", json={"real": True}, headers=_headers("manager"))
    finally:
        eval_api.run_benchmark = original
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "real"
    # real 报告单独落盘；mock 文件不写（本测试目录隔离，未跑过 mock）
    assert report_mod.report_file(True).exists()
    assert not report_mod.report_file(False).exists()


def test_csr_cannot_run():
    r = client.post("/api/v1/eval/run", json={"real": False}, headers=_headers("csr"))
    assert r.status_code == 403


def test_real_run_without_llm_key_returns_400(isolated_eval_dir, monkeypatch):
    """未配置 LLM_API_KEY 时 real 模式跑批 -> 400 LLM_NOT_CONFIGURED（而非 500）。"""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "llm_api_key", "")
    r = client.post("/api/v1/eval/run", json={"real": True}, headers=_headers("manager"))
    assert r.status_code == 400
    assert r.json()["code"] == "LLM_NOT_CONFIGURED"


def test_csr_can_read_report(isolated_eval_dir):
    assert client.post("/api/v1/eval/run", json={"real": False}, headers=_headers("manager")).status_code == 200
    r = client.get("/api/v1/eval/report", headers=_headers("csr"))
    assert r.status_code == 200
    assert r.json()["aggregate"]["count"] == 10