"""L-5 修复：凭证上传返回「可服务的 URL 路径 + 静态挂载」。

旧缺陷：_save_upload 落库相对路径 `uploads/<uuid>.ext`（无前导斜杠），
后端也没有挂载静态目录 —— 客户端拿到的 image_url 既不是 URL 也无法通过
任意 HTTP 端点取回文件，<img>/下载一律 404。

验证三件事：
1. create_case 上传凭证 -> CaseEvidence.image_url 以 `/uploads/` 开头；
2. 该 URL 经 GET /uploads/<file> 能取回上传字节（静态挂载生效）；
3. storage.image_url_to_disk 对新格式 `/uploads/x` 与历史相对格式 `uploads/x`
   都能还原为 UPLOAD_DIR 下的磁盘路径（OCR Worker 磁盘读取兼容存量数据）。
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.domain.models import AuditLog, CaseEvidence, RefundCase
from app.infrastructure.storage import UPLOAD_DIR, image_url_to_disk
from app.main import app

client = TestClient(app)

MARKER = f"up-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def cleanup():
    yield
    db = SessionLocal()
    try:
        case_ids = [
            c.id for c in db.query(RefundCase).filter_by(applicant_id=MARKER).all()
        ]
        for cid in case_ids:
            for model in (AuditLog, CaseEvidence):
                db.query(model).filter(model.case_id == cid).delete(
                    synchronize_session=False
                )
            db.query(RefundCase).filter(RefundCase.id == cid).delete(
                synchronize_session=False
            )
        db.commit()
    finally:
        db.close()
    # 清理测试落盘的 uploads 文件
    for f in UPLOAD_DIR.glob(f"{MARKER}-*"):
        f.unlink(missing_ok=True)


def _staff_token() -> str:
    r = client.post("/api/v1/auth/login", json={"username": "csr", "password": "csr123"})
    assert r.status_code == 200
    return r.json()["access_token"]


def _upload_case(content: bytes = b"\x89PNG\r\n\x1a\nfakepng") -> dict:
    r = client.post(
        "/api/v1/cases",
        data={
            "applicant_id": MARKER,
            "order_id": "order-1",
            "applicant_amount": 12_800,
            "actual_amount": 12_800,
            "description": "商品破损，申请退款",
        },
        files={"image": (f"{MARKER}-receipt.png", content, "image/png")},
        headers={"Authorization": f"Bearer {_staff_token()}"},
    )
    assert r.status_code == 202, r.text
    return r.json()


class TestUploadServableUrl:
    def test_image_url_is_absolute_servable_path(self):
        """落库 image_url 以 /uploads/ 开头，且静态端点可回源取到原字节。"""
        _upload_case()

        db = SessionLocal()
        try:
            case = db.query(RefundCase).filter_by(applicant_id=MARKER).first()
            assert case is not None
            ev = db.query(CaseEvidence).filter_by(case_id=case.id).first()
            assert ev is not None
            assert ev.image_url.startswith("/uploads/"), ev.image_url
        finally:
            db.close()

        # 静态挂载：GET /uploads/<file> 返回 200 且字节一致
        r = client.get(ev.image_url)
        assert r.status_code == 200
        assert r.content == b"\x89PNG\r\n\x1a\nfakepng"

    def test_case_detail_exposes_servable_url(self):
        """案件详情 evidences[0].image_url 直接可为前端 img src。"""
        created = _upload_case()
        r = client.get(
            f"/api/v1/cases/{created['case_id']}",
            headers={"Authorization": f"Bearer {_staff_token()}"},
        )
        assert r.status_code == 200
        evs = r.json()["evidences"]
        assert evs and evs[0]["image_url"].startswith("/uploads/")

    def test_missing_static_file_404(self):
        """静态目录外路径/不存在文件返回 404（不泄露目录内容）。"""
        r = client.get("/uploads/not-exists-404.png")
        assert r.status_code == 404


class TestPathNormalization:
    def test_new_and_legacy_formats_map_to_same_disk_path(self):
        """/uploads/x.jpg 与历史相对 uploads/x.jpg 归一为同一磁盘路径。"""
        new = image_url_to_disk("/uploads/abc.jpg")
        legacy = image_url_to_disk("uploads/abc.jpg")
        assert new == legacy
        assert new == str(UPLOAD_DIR / "abc.jpg")

    def test_empty_url_returns_empty(self):
        assert image_url_to_disk("") == ""