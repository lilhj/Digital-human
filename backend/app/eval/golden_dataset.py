"""Golden Dataset（需求文档 §2.2）：10 个用例覆盖退赔全部业务边界。

字段语义：
- id / scenario / kind（decision=决策边界, security=金额边界安全, quality=理由质量）
- case 输入（金额分/风险分/舆情分/OCR置信度/凭证状态/描述）
- expected 期望决策（APPROVE/REJECT/HUMAN_REVIEW/REJECT_API）
- note 期望理由要点
"""
GOLDEN_CASES: list[dict] = [
    {
        "id": "G01",
        "scenario": "小额低风险",
        "kind": "decision",
        "case": {
            "amount_cent": 12800,           # 128 元
            "actual_amount_cent": 12800,
            "fraud_score": 0.1,
            "sentiment_score": 0.1,
            "ocr_confidence": 0.95,         # OCR 高置信
            "evidence_status": "OK",
            "description": "收到商品破损，申请退款。",
        },
        "expected": "APPROVE",
        "note": "低风险自动通过（秒退）",
    },
    {
        "id": "G02",
        "scenario": "超300元限额",
        "kind": "decision",
        "case": {
            "amount_cent": 35000,           # 350 元
            "actual_amount_cent": 35000,
            "fraud_score": 0.1,
            "sentiment_score": 0.1,
            "ocr_confidence": 0.9,
            "evidence_status": "OK",
            "description": "商品质量问题申请退款。",
        },
        "expected": "HUMAN_REVIEW",
        "note": "金额超 300 元限额强制挂起",
    },
    {
        "id": "G03",
        "scenario": "高风险恶意退款",
        "kind": "decision",
        "case": {
            "amount_cent": 9900,
            "actual_amount_cent": 9900,
            "fraud_score": 0.9,             # 高风险
            "sentiment_score": 0.1,
            "ocr_confidence": 0.9,
            "evidence_status": "OK",
            "description": "疑似薅羊毛，历史多次退款。",
        },
        "expected": "REJECT",
        "note": "风险分 > 0.5 拒绝",
    },
    {
        "id": "G04",
        "scenario": "OCR低置信",
        "kind": "decision",
        "case": {
            "amount_cent": 19900,
            "actual_amount_cent": 19900,
            "fraud_score": 0.1,
            "sentiment_score": 0.1,
            "ocr_confidence": 0.3,          # < 0.5 强制人工
            "evidence_status": "OK",
            "description": "凭证模糊，退款。",
        },
        "expected": "HUMAN_REVIEW",
        "note": "OCR 置信度过低",
    },
    {
        "id": "G05",
        "scenario": "OCR中等置信",
        "kind": "decision",
        "case": {
            "amount_cent": 19900,
            "actual_amount_cent": 19900,
            "fraud_score": 0.1,
            "sentiment_score": 0.1,
            "ocr_confidence": 0.6,          # 0.5~0.7 预警
            "evidence_status": "OK",
            "description": "凭证可辨，退款。",
        },
        "expected": "HUMAN_REVIEW",
        "note": "OCR 置信度预警",
    },
    {
        "id": "G06",
        "scenario": "舆情升级HIGH",
        "kind": "decision",
        "case": {
            "amount_cent": 9900,
            "actual_amount_cent": 9900,
            "fraud_score": 0.1,
            "sentiment_score": 0.9,         # 舆情分高
            "ocr_confidence": 0.9,
            "evidence_status": "OK",
            "description": "情绪激烈，扬言投诉平台，强烈要求退款。",
        },
        "expected": "HUMAN_REVIEW",
        "note": "舆情风险高，需人工安抚决策",
    },
    {
        "id": "G07",
        "scenario": "中风险区间",
        "kind": "decision",
        "case": {
            "amount_cent": 9900,
            "actual_amount_cent": 9900,
            "fraud_score": 0.35,            # 0.2~0.5
            "sentiment_score": 0.1,
            "ocr_confidence": 0.9,
            "evidence_status": "OK",
            "description": "描述前后矛盾，退款。",
        },
        "expected": "HUMAN_REVIEW",
        "note": "风险分 0.2~0.5 人工复核",
    },
    {
        "id": "G08",
        "scenario": "空凭证",
        "kind": "decision",
        "case": {
            "amount_cent": 9900,
            "actual_amount_cent": 9900,
            "fraud_score": 0.1,
            "sentiment_score": 0.1,
            "ocr_confidence": None,
            "evidence_status": "EMPTY",     # 无图片/无描述
            "description": "",
        },
        "expected": "HUMAN_REVIEW",
        "note": "未上传可识别凭证，兜底人工",
    },
    {
        "id": "G09",
        "scenario": "金额超实付",
        "kind": "security",
        "case": {
            "amount_cent": 20000,           # 200 元申请
            "actual_amount_cent": 10000,    # 实付 100 元
            "fraud_score": 0.1,
            "sentiment_score": 0.1,
            "ocr_confidence": 0.9,
            "evidence_status": "OK",
            "description": "申请金额超实付。",
        },
        "expected": "REJECT_API",           # 接口层 422 拒绝（D-012）
        "note": "退款金额 > 实付金额，接口硬校验",
    },
    {
        "id": "G10",
        "scenario": "正常小额退货",
        "kind": "decision",
        "case": {
            "amount_cent": 9900,            # 99 元
            "actual_amount_cent": 9900,
            "fraud_score": 0.1,
            "sentiment_score": 0.1,
            "ocr_confidence": 0.95,
            "evidence_status": "OK",
            "description": "7天无理由退货，凭证齐全。",
        },
        "expected": "APPROVE",
        "note": "低风险自动通过",
    },
]


def load_cases() -> list[dict]:
    return GOLDEN_CASES
