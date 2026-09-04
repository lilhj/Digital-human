"""沙箱适配层测试（工单5 移植）：路径净化 / 注入拦截 / Excel 解析归一。

- sanitize_path 拒 ../、绝对路径、接受相对路径
- guard_escape_attempt 拦截系统命令 / 路径穿越 / 网络外带
- parse_approval_excel_local 解析 + _normalize_rows 列归一与动作映射
"""
import pytest
from pathlib import Path

from app.sandbox.adapter import (
    _ACTION_MAP,
    _normalize_rows,
    guard_escape_attempt,
    parse_approval_excel_local,
    sanitize_path,
)


def test_sanitize_path_rejects_traversal(tmp_path):
    with pytest.raises(ValueError):
        sanitize_path("../etc/passwd", tmp_path)
    with pytest.raises(ValueError):
        sanitize_path("sub/../../outside", tmp_path)


def test_sanitize_path_rejects_absolute(tmp_path):
    with pytest.raises(ValueError):
        sanitize_path("C:/Windows/win.ini", tmp_path)
    with pytest.raises(ValueError):
        sanitize_path("/etc/hosts", tmp_path)


def test_sanitize_path_accepts_relative(tmp_path):
    (tmp_path / "in").mkdir()
    safe = sanitize_path("in/bills.xlsx", tmp_path)
    assert safe == (tmp_path / "in" / "bills.xlsx").resolve()


def test_guard_escape_attempt_blocks_system_command():
    assert guard_escape_attempt("运行 import os; print(os.system('whoami'))") is False
    assert guard_escape_attempt("读取 C:/Windows/win.ini 的内容") is False
    assert guard_escape_attempt("用 curl 拉取外部数据") is False


def test_guard_escape_attempt_allows_normal():
    assert guard_escape_attempt("请读取 bills.xlsx 并统计工作表数量") is True


def test_normalize_rows_maps_actions():
    rows = [
        {"case_id": 101, "审批动作": "同意", "审批意见": "情况属实"},
        {"case_id": 102, "action": "REJECT", "comment": "证据不足"},
        {"case_id": 103, "审批动作": "未知", "审批意见": ""},
        {},  # 空行忽略
    ]
    normalized = _normalize_rows(rows)
    assert len(normalized) == 3
    assert normalized[0]["action"] == "APPROVE"
    assert normalized[0]["case_id"] == "101"
    assert normalized[0]["comment"] == "情况属实"
    assert normalized[1]["action"] == "REJECT"
    assert normalized[2]["action"] is None
    assert "error" in normalized[2]


def test_action_map_covers_expected_values():
    # 需求文档 §3.1：固定枚举 同意/拒绝
    assert _ACTION_MAP["同意"] == "APPROVE"
    assert _ACTION_MAP["拒绝"] == "REJECT"


def test_parse_approval_excel_local(tmp_path):
    """SANDBOX_MODE=off 宿主机直读：生成一份审批 Excel 并解析。"""
    from openpyxl import Workbook

    path = tmp_path / "approval.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["case_id", "审批动作", "审批意见"])
    ws.append([200, "同意", "ok"])
    ws.append([201, "拒绝", "no"])
    wb.save(path)

    rows = parse_approval_excel_local(path)
    assert len(rows) == 2
    assert rows[0]["action"] == "APPROVE"
    assert rows[1]["action"] == "REJECT"
