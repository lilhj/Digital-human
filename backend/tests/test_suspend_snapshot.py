"""挂起快照双写单测（v2.0 §11）：_write_suspend_snapshot / _delete_suspend_snapshot。

用内存假 Redis 隔离，验证：
1. 挂起时写入 refund:suspend:{case_id}，TTL=7天，内容为 state 中敏感字段脱敏后的 JSON；
2. 恢复后删除该 key；
3. Redis 异常时降级（只告警，不抛出）。
"""

import json

import pytest

from app.security.dlp import RuleBasedDlpProvider
from app.workflow import nodes


class _FakeRedis:
    """极简内存 Redis：仅实现本测试用到的 set/delete/get/ex。"""

    def __init__(self):
        self._store = {}
        self._ttl = {}

    def set(self, key, value, ex=None):
        self._store[key] = value
        self._ttl[key] = ex
        return True

    def get(self, key):
        return self._store.get(key)

    def delete(self, key):
        return self._store.pop(key, None) is not None

    def ttl(self, key):
        return self._ttl.get(key)


@pytest.fixture
def fake_redis(monkeypatch):
    fr = _FakeRedis()
    monkeypatch.setattr(nodes, "get_redis", lambda: fr)
    return fr


def _sample_state(case_id: int) -> dict:
    return {
        "case_id": case_id,
        "amount_cent": 12_800,
        "actual_amount_cent": 12_800,
        "description": "商品破损申请退款",
        "decision": "HUMAN_REVIEW",
        "review_reason": "金额超过自动阈值",
    }


def test_write_snapshot_sets_key_with_ttl(fake_redis):
    case_id = 999
    state = _sample_state(case_id)
    nodes._write_suspend_snapshot(case_id, state)

    key = f"refund:suspend:{case_id}"
    assert fake_redis.get(key) is not None
    # TTL = 7 天 = 604800 秒
    assert fake_redis.ttl(key) == 7 * 24 * 60 * 60
    # 内容为 state 的 JSON，可反序列化且字段一致
    restored = json.loads(fake_redis.get(key))
    assert restored["case_id"] == case_id
    assert restored["amount_cent"] == 12_800


def test_delete_snapshot_removes_key(fake_redis):
    case_id = 1001
    nodes._write_suspend_snapshot(case_id, _sample_state(case_id))
    assert fake_redis.get(f"refund:suspend:{case_id}") is not None

    nodes._delete_suspend_snapshot(case_id)
    assert fake_redis.get(f"refund:suspend:{case_id}") is None


def test_write_snapshot_degrades_on_redis_error(monkeypatch):
    """Redis 抛异常时只告警不阻断（核心挂起链路不能因辅助缓存失败）。"""

    class _BoomRedis:
        def set(self, *a, **k):
            raise RuntimeError("redis down")

        def delete(self, *a, **k):
            raise RuntimeError("redis down")

    monkeypatch.setattr(nodes, "get_redis", lambda: _BoomRedis())
    # 不抛异常即通过
    nodes._write_suspend_snapshot(1, _sample_state(1))
    nodes._delete_suspend_snapshot(1)


def test_snapshot_masks_pii_before_write(monkeypatch, fake_redis):
    """Redis 快照不得留存明文 PII：evidence_text（原始 OCR）/description 写入前先脱敏。

    L-7 修复验证：写出的 payload 中原始手机号/身份证必须消失，掩码形式留存；
    非敏感字段（case_id/amount_cent 等）完整保留，供运维排查/导出仍可用。
    """
    monkeypatch.setattr(nodes, "dlp_provider", RuleBasedDlpProvider())
    case_id = 1002
    state = {
        "case_id": case_id,
        "amount_cent": 12_800,
        "description": "联系电话 13800000000 请尽快处理",
        "evidence_text": "发票 13800000000 身份证 110101199001011234 实付128元",
        "decision": "HUMAN_REVIEW",
    }
    nodes._write_suspend_snapshot(case_id, state)

    stored = fake_redis.get(f"refund:suspend:{case_id}")
    assert stored is not None
    snapshot = json.loads(stored)

    # 明文绝不落 Redis
    assert "13800000000" not in stored
    assert "110101199001011234" not in stored
    # 掩码形态留存，脱敏后仍可读
    assert "138****0000" in snapshot["evidence_text"]
    assert "110101********1234" in snapshot["evidence_text"]
    assert "138****0000" in snapshot["description"]
    # 非敏感字段完整保留
    assert snapshot["case_id"] == case_id
    assert snapshot["amount_cent"] == 12_800
    assert snapshot["decision"] == "HUMAN_REVIEW"

    # 幂等：description 入口已脱敏，快照再走一次 mask 不会破坏既有掩码
    assert nodes.dlp_provider.mask(snapshot["description"]) == snapshot["description"]
    assert nodes.dlp_provider.mask(snapshot["evidence_text"]) == snapshot["evidence_text"]
