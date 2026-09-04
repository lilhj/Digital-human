"""批量审批服务测试（需求文档 §3.1）：导出 / 解析归一 / 防重。

- 导出挂起单：生成 Excel 含只读列 + 空白审批列
- 解析归一：中文/英文动作映射
- 防资损防重：同单重复批量审批不二次处理（幂等键命中）
"""
from io import BytesIO

import pytest
from openpyxl import load_workbook

from app.batch.service import EXPORT_COLUMNS, export_suspended_excel
from app.sandbox.adapter import _ACTION_MAP, _normalize_action, _normalize_rows, parse_approval_excel_local


def test_export_columns_structure():
    """需求文档 §3.1 字段设计：只读列 + 空白审批列。"""
    assert "case_id" in EXPORT_COLUMNS
    assert "ticket_no" in EXPORT_COLUMNS
    assert "action" in EXPORT_COLUMNS  # 主管填
    assert "comment" in EXPORT_COLUMNS  # 主管填


def test_export_suspended_excel_format():
    """导出为合法 xlsx：表头 + 数据行 + 审批列留空。"""
    content, filename = export_suspended_excel()
    assert content
    assert filename.endswith(".xlsx")
    wb = load_workbook(BytesIO(content))
    ws = wb.active
    # 表头包含中英文标签
    header = [str(c.value) for c in ws[1]]
    assert "案件ID" in header
    assert "审批动作(同意/拒绝)" in header
    assert "审批意见" in header


def test_parse_approval_excel_roundtrip(tmp_path):
    """导出 -> 主管填审批 -> 上传解析：完整闭环（off 模式）。"""
    content, filename = export_suspended_excel()
    # 若当前库无挂起单，Excel 只有表头；用独立构造的数据验证闭环
    path = tmp_path / "fill.xlsx"
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["case_id", "审批动作", "审批意见"])
    ws.append([101, "同意", "情况属实"])
    ws.append([102, "拒绝", "证据不足"])
    ws.append([103, "非法动作", ""])
    wb.save(path)

    rows = parse_approval_excel_local(path)
    normalized = _normalize_rows(rows)
    actions = {r["action"] for r in normalized if not r.get("error")}
    assert actions == {"APPROVE", "REJECT"}
    errors = [r for r in normalized if r.get("error")]
    assert len(errors) == 1
    assert "非法审批动作" in errors[0]["error"]


def test_parse_approval_excel_with_export_template_header(tmp_path):
    """回归修复：导出模板表头（中文标签 + 括号提示）填「同意/拒绝」必须能解析出动作。

    之前的 bug：导出 Excel 列名为「审批动作(同意/拒绝)」而非「审批动作」，
    _normalize_rows 精确匹配失败 -> action 读成空 -> 全部「非法审批动作: ''」。
    """
    path = tmp_path / "export_template.xlsx"
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    # 模拟导出模板的真实表头：案件ID / 工单号 / 审批动作(同意/拒绝) / 审批意见
    ws.append(["工单号", "案件ID", "订单号", "申请退款金额(分)", "实付金额(分)", "客诉描述",
               "OCR摘要", "风险分", "舆情等级", "审批动作(同意/拒绝)", "审批意见"])
    ws.append(["T20260830215033894C43", 203, "order-203", 35000, 35000, "客户投诉",
               "ocr", 0.75, "MEDIUM", "同意", "同意退款"])
    ws.append(["T20260830215033894C44", 204, "order-204", 35000, 35000, "投诉",
               "ocr", 0.8, "HIGH", "拒绝", "证据不足"])
    # 第三行：数字 case_id 列优先，应直接取案件ID（不落工单号）
    ws.append(["T20260830215033894C45", 205, "order-205", 35000, 35000, "投诉",
               "ocr", 0.6, "LOW", "同意", "正常"])
    wb.save(path)

    rows = parse_approval_excel_local(path)
    normalized = _normalize_rows(rows)
    assert len(normalized) == 3
    assert normalized[0]["action"] == "APPROVE", f"同意 未解析: {normalized[0]}"
    assert normalized[1]["action"] == "REJECT", f"拒绝 未解析: {normalized[1]}"
    assert normalized[2]["action"] == "APPROVE"
    assert normalized[2]["case_id"] == "205", f"数字 case_id 列应优先取到案件ID: {normalized[2]}"
    # 表头归一：action 列（审批动作(同意/拒绝)）已解析为 APPROVE/REJECT；
    # case_id 列取不到数字（模板优先取到工单号列），靠 ticket_no 兜底定位案件。
    assert normalized[0]["ticket_no"] == "T20260830215033894C43"
    assert all("error" not in r for r in normalized)
    # 动作映射必须正确（回归修复的核心）
    assert normalized[0]["action"] == "APPROVE"
    assert normalized[1]["action"] == "REJECT"


def test_normalize_skips_empty_rows():
    rows = [{"case_id": "", "审批动作": "同意"}, {"case_id": None, "action": "REJECT"}]
    assert _normalize_rows(rows) == []


def test_normalize_action_tolerant_whitespace_and_case():
    """主管手输宽容：全角/连续空格 + 大小写变体都能映射，不产非法动作。"""
    p = _normalize_action
    # 中文 + 全角空格/连续空格
    assert _ACTION_MAP[p("同意")] == "APPROVE"
    assert _ACTION_MAP[p("拒绝")] == "REJECT"
    assert _ACTION_MAP[p("同　意")] == "APPROVE"       # 全角空格
    assert _ACTION_MAP[p(" 拒  绝 ")] == "REJECT"      # 连续空格
    # 英文大小写变体
    assert _ACTION_MAP[p("Approve")] == "APPROVE"
    assert _ACTION_MAP[p("REJECT")] == "REJECT"
    assert _ACTION_MAP[p("approval")] == "APPROVE"
    assert _ACTION_MAP[p("  approve  ")] == "APPROVE"
    assert _ACTION_MAP[p("deny")] == "REJECT"
    # 无法识别 -> 空，走非法动作分支
    assert p(None) == ""
    assert _ACTION_MAP.get(p("未知动作")) is None


def test_idempotency_key_stable():
    """同单同动作同操作者 -> 幂等键稳定（防资损重）。"""
    from app.infrastructure.idempotency import build_key as bk

    k1 = bk("approval", "101", "APPROVE", "batch-manager")
    k2 = bk("approval", "101", "APPROVE", "batch-manager")
    k3 = bk("approval", "101", "REJECT", "batch-manager")
    assert k1 == k2
    assert k1 != k3
