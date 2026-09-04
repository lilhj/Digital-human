"""买家侧 RAG 客服聊天编排：路由判定（政策/数据/拒答红线）+ 答案生成。

流程（POST /api/v1/buyer/chat {question, order_id?}）：
  1. 红线词表检测（附录 B 内部信息套问 / 诱导越权执行退款）→ REFUSE 转人工
  2. 数据型路由（我的订单/发货/物流…）→ TOOL：返回该买家订单真实状态 + 跳转按钮
  3. RAG 检索：top1 原始分 < MIN_SCORE → REFUSE（知识库不覆盖）
  4. 政策型：LLM 依据知识库条目生成答案并标注引用；LLM 不可用/失败
     → 降级用 top chunk 原文直接作答（诚实原则：不编造政策数字）

安全边界：附录 B 内部信息（自动批准限额/风控阈值/OCR 阈值/规则优先级/沙箱 DLP）
绝不出现在答案中；聊天只读，无任何退款执行能力（呼应 Tool 白名单设计）。
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.llm import LLMClient, LLMOutputError
from app.core.database import get_db
from app.core.security import (
    TOKEN_TYPE_BUYER,
    bearer_scheme,
    decode_token,
)
from app.domain.models import Customer, Order, OrderStatus
from app.rag import retriever

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/buyer", tags=["buyer-chat"])

# ---------- 红线词表（安全红线，先于一切判定） ----------

# 附录 B 内部信息主题词：套问内部决策阈值/风控细节 → 拒答转人工
_REFUSE_INTERNAL_KEYWORDS = [
    "金额上限", "自动通过", "自动批准", "自动审批", "限额",
    "阈值", "风控", "风险分", "置信度", "OCR", "规则优先级",
    "沙箱", "DLP", "决策引擎",
]

# 诱导越权执行退款：承诺/模拟/直接办理 → 拒答并引导走售后流程。
# 注意精确化：不能用宽泛的「直接退」（会误伤 KB17 合法问题
# 「信用卡退款是直接退现金到我卡里吗？」），需搭配执行动词/对象。
_REFUSE_EXECUTE_KEYWORDS = [
    "不用审核", "不用审批", "跳过审核", "跳过审批",
    "帮我退了", "帮我把这", "直接把这笔", "把这笔订单",
    "退款办了", "直接办了", "直接办理", "强制执行", "替我退了",
]

# ---------- 数据型路由（知识库答非所问 → 走工具通道） ----------

_DATA_ROUTE_KEYWORDS = [
    "我的订单", "订单状态", "什么时候发货", "发货了吗", "发货了没",
    "物流", "快递到哪", "到哪了", "订单什么时候", "我的退款",
    "退款到账了吗", "发货没有", "发货进度",
]

# 订单状态 -> 中文标签（与前端 STATUS_LABEL 一致）
_ORDER_STATUS_LABEL = {
    OrderStatus.PENDING_PAYMENT.value: "待支付",
    OrderStatus.PAID.value: "待发货",
    OrderStatus.SHIPPED.value: "已发货",
    OrderStatus.COMPLETED.value: "已完成",
    OrderStatus.REFUNDING.value: "退款中",
    OrderStatus.REFUNDED.value: "已退款",
    OrderStatus.EXCHANGING.value: "换货中",
    OrderStatus.CLOSED.value: "已关闭",
}


class ChatRequest(BaseModel):
    model_config = {"extra": "forbid"}

    question: str = Field(min_length=1, max_length=500)
    order_id: int | None = None  # 数据型问题可指定订单；缺省返回最近一笔


def _refuse_type(question: str) -> str | None:
    """红线判定：返回拒答原因类别（internal / execute），无红线返回 None。"""
    for kw in _REFUSE_INTERNAL_KEYWORDS:
        if kw in question:
            return "internal"
    for kw in _REFUSE_EXECUTE_KEYWORDS:
        if kw in question:
            return "execute"
    return None


def _is_data_question(question: str) -> bool:
    return any(kw in question for kw in _DATA_ROUTE_KEYWORDS)


def _recent_order(db: Session, customer: Customer, order_id: int | None) -> Order | None:
    """取该买家最近一笔订单；指定 order_id 时限定归属（M-1 越权防护）。"""
    q = db.query(Order).filter(Order.customer_id == customer.id)
    if order_id is not None:
        return q.filter(Order.id == order_id).first()
    return q.order_by(Order.id.desc()).first()


def _tool_payload(db: Session, customer: Customer, order_id: int | None) -> dict:
    """数据型问题：返回该买家真实订单状态 + 跳转按钮。"""
    order = _recent_order(db, customer, order_id)
    if order is None:
        return {
            "type": "tool",
            "message": "您还没有相关订单。下单后可在「我的订单」中查看实时状态。",
            "button": {"label": "去逛逛商品", "href": "/buyer/products"},
            "orders": [],
        }
    orders = db.query(Order).filter(Order.customer_id == customer.id).order_by(Order.id.desc()).limit(5).all()
    return {
        "type": "tool",
        "message": (
            f"您最近一笔订单 #{order.id} 当前状态为「{_ORDER_STATUS_LABEL.get(order.status, order.status)}」。"
            "订单与物流以「我的订单」实时状态为准。"
        ),
        "button": {"label": "查看我的订单", "href": "/buyer/orders"},
        "orders": [
            {
                "id": o.id,
                "order_no": o.order_no,
                "status": o.status,
                "status_label": _ORDER_STATUS_LABEL.get(o.status, o.status),
                "total_cents": o.total_cents,
            }
            for o in orders
        ],
    }


def _snippet_answer(hits: list[tuple[retriever.Chunk, float]]) -> tuple[str, list[str]]:
    """LLM 不可用/失败时的降级答案：拼接 top-3 chunk 知识库原文（诚实，不编造）。

    只取 top1 会漏要点（如「无理由 + 三包」双通道只给一条 FAQ）——按来源条目去重拼接，
    每条保留其标题行便于买家理解来源。
    """
    sources: list[str] = []
    parts: list[str] = []
    for c, _ in hits[:3]:
        if c.entry_id in sources:
            continue
        sources.append(c.entry_id)
        parts.append(c.text)
    return "\n\n".join(parts), sources


def _llm_answer(question: str, hits: list[tuple[retriever.Chunk, float]]) -> tuple[str, list[str]] | None:
    """LLM 依据知识库条目生成答案（带引用）。失败返回 None，由调用方降级。"""
    context = "\n\n".join(f"[{c.entry_id}] {c.title}\n{c.text}" for c, _ in hits[:3])
    system = (
        "你是电商平台智能客服。只依据提供的知识库条目回答买家问题："
        "不得编造政策数字、不得透露内部信息、不得承诺退款执行。"
        "答案须标注引用的知识库条目编号。"
        "只输出 JSON：{\"answer\": \"面向买家的友好答复\", \"citations\": [\"条目编号\"]}"
    )
    try:
        llm = LLMClient()
        data = llm.chat_json(system, f"买家问题：{question}\n\n知识库条目：\n{context}")
        answer = str(data.get("answer", "")).strip()
        citations = [str(c) for c in data.get("citations", [])][:5]
        if not answer:
            return None
        return answer, citations
    except LLMOutputError as e:
        logger.warning("客服回答 LLM 失败，降级知识库原文: %s", e)
        return None


def _refuse_payload(kind: str) -> dict:
    if kind == "execute":
        return {
            "type": "refuse",
            "reason": "execute",
            "message": "智能客服没有退款执行权限，无法代您操作。请在「我的订单 → 申请售后」中提交退款申请，平台会按流程人工/自动审核。",
        }
    return {
        "type": "refuse",
        "reason": "internal",
        "message": "该问题涉及平台内部决策信息，超出客服知识库范围，已为您转接人工客服。",
    }


def handle_chat(
    question: str,
    customer: Customer | None,
    db: Session,
    order_id: int | None = None,
    *,
    force_fallback: bool = False,
) -> dict:
    """聊天编排主流程：返回三态（answer / tool / refuse）统一结构。

    force_fallback=True：跳过 LLM 直接走知识库原文降级路径（确定性），
    供 RAG 评测（app.eval.rag_benchmark）离线跑批使用，避免真调 LLM。
    """
    # 1. 安全红线（附录 B + 诱导执行）→ 强制拒答，零容忍
    red = _refuse_type(question)
    if red:
        return _refuse_payload(red)

    # 2. 数据型路由：我的订单/发货/物流 → 工具通道（真实状态 + 跳转按钮）
    if _is_data_question(question):
        if customer is None:
            return {
                "type": "tool",
                "message": "查看订单状态需要登录买家账号。",
                "button": {"label": "去登录", "href": "/buyer/login"},
                "orders": [],
            }
        return _tool_payload(db, customer, order_id)

    # 3. 政策型：RAG 检索 + 拒答阈值（未扩展原始分）
    raw = retriever.search(question, expand=False)
    if not raw or raw[0][1] < retriever.MIN_SCORE:
        return {
            "type": "refuse",
            "reason": "out_of_kb",
            "message": "该问题超出知识库范围，已为您转接人工客服。",
        }

    # 4. 生成答案：LLM 带引用，失败降级知识库原文
    hits = retriever.search(question, expand=True)
    if not force_fallback:
        llm_out = _llm_answer(question, hits)
        if llm_out:
            answer, citations = llm_out
            return {"type": "answer", "answer": answer, "sources": citations, "fallback": False}
    answer, sources = _snippet_answer(hits)
    return {"type": "answer", "answer": answer, "sources": sources, "fallback": True}


def get_optional_customer(
    db: Annotated[Session, Depends(get_db)],
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> Customer | None:
    """可选买家鉴权：未登录也能聊政策问题；数据型路由需登录。

    不能直接 Depends(get_current_customer)——它在依赖解析阶段对无/错 token 抛
    401，handler 无法捕获、会把整个聊天入口打死。这里自解 token 并吞异常：
    无 token / 非 BUYER / 买家不存在 均返回 None（政策问答允许游客）。
    """
    if creds is None:
        return None
    try:
        payload = decode_token(creds.credentials)
    except Exception:
        return None
    if payload.get("type") != TOKEN_TYPE_BUYER:
        return None
    return db.query(Customer).filter_by(phone=payload["sub"]).first()


@router.post("/chat")
def buyer_chat(
    body: ChatRequest,
    customer: Annotated[Customer | None, Depends(get_optional_customer)],
    db: Annotated[Session, Depends(get_db)],
):
    """买家智能客服（RAG）：政策走知识库、数据走订单工具、红线拒答转人工。"""
    return handle_chat(body.question, customer, db, order_id=body.order_id)
