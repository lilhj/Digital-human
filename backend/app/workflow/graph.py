"""LangGraph 图构建与执行入口（裁决 D-005：PostgreSQL Checkpointer）。

结构：
    START -> intake --BLOCK(安全网关拦截)--> decision（跳过意图/三查/OCR/风控，脏数据不进模型）
                     --PASS(正常)--> intent -> order_verify(硬闸) --REJECT/REVIEW--> decision
                                                                    --PASS--> evidence -> fraud -> sentiment -> decision
    decision --HUMAN_REVIEW--> human_review(interrupt 挂起) -> finalize -> END
    decision --APPROVE/REJECT--> finalize -> END
"""
import logging

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.infrastructure.checkpoint import create_checkpointer
from app.policy.decision import DECISION_APPROVE, DECISION_HUMAN_REVIEW, DECISION_REJECT
from app.workflow.nodes import (
    decision_node,
    evidence_node,
    finalize_node,
    fraud_node,
    human_review_node,
    intake_node,
    intent_node,
    order_verify_node,
    sentiment_node,
)
from app.workflow.state import RefundWorkflowState

logger = logging.getLogger("workflow")


def build_graph() -> StateGraph:
    g = StateGraph(RefundWorkflowState)
    g.add_node("intake", intake_node)
    g.add_node("intent", intent_node)
    g.add_node("order_verify", order_verify_node)
    g.add_node("evidence", evidence_node)
    g.add_node("fraud", fraud_node)
    g.add_node("sentiment", sentiment_node)
    g.add_node("decision", decision_node)
    g.add_node("human_review", human_review_node)
    g.add_node("finalize", finalize_node)

    g.add_edge(START, "intake")
    # 工单6 安全网关短路：Critic BLOCK（越权/注入）在 intake 即定性，直接跳过
    # 意图/三查/OCR/风控等下游 —— 脏描述不喂给任何模型/OCR（省钱且避免误判），
    # 直奔 decision（其 BLOCK 分支强制转人工复核）。对齐 order_verify 硬闸的短路范式。
    g.add_conditional_edges("intake", _route_from_intake)
    # 工单8 双层意图路由：换货/意图不明（intent_fallback）→ 直接转人工复核（MVP 不自动处理换货）；
    # 退款/退货 → 进入订单三查硬闸常规决策流
    g.add_conditional_edges("intent", _route_from_intent)
    # 三查硬闸：伪造/重复订单直接拒、不一致转人工 —— 跳过下游 OCR/LLM，直奔 decision
    g.add_conditional_edges("order_verify", _route_from_order_verify)
    g.add_edge("evidence", "fraud")
    g.add_edge("fraud", "sentiment")
    g.add_edge("sentiment", "decision")
    g.add_conditional_edges("decision", _route_from_decision)
    g.add_edge("human_review", "finalize")
    g.add_edge("finalize", END)
    return g


def _route_from_intake(state: RefundWorkflowState) -> str:
    """工单6 安全网关短路路由：Critic BLOCK 是最高优先级定性。

    BLOCK → 直接 decision（其 BLOCK 分支强制转人工复核），跳过
    intent / order_verify / evidence(OCR) / fraud+sentiment(LLM)——脏数据
    不进任何模型，既不白花 token/OCR 时延，也避免注入描述污染自动判定。
    PASS → 正常进入意图识别。
    """
    if state.get("security_action") == "BLOCK":
        return "decision"
    return "intent"


def _route_from_intent(state: RefundWorkflowState) -> str:
    """工单8 意图路由：EXCHANGE/UNKNOWN（intent_fallback）→ 人工复核；其余走订单三查。"""
    if state.get("intent_fallback"):
        return "human_review"
    return "order_verify"


def _route_from_order_verify(state: RefundWorkflowState) -> str:
    """三查结果路由：REJECT/REVIEW 已定论，直奔 decision；PASS 走正常取证链路。"""
    if state.get("order_verify_result") in ("REJECT", "REVIEW"):
        return "decision"
    return "evidence"


def _route_from_decision(state: RefundWorkflowState) -> str:
    if state.get("decision") == DECISION_HUMAN_REVIEW:
        return "human_review"
    return "finalize"


def compile_graph():
    """编译图：每次调用创建独立 PostgresSaver 连接（线程安全），检查点存 PG。"""
    return build_graph().compile(checkpointer=create_checkpointer())


def run_workflow(case_id: int, trace_id: str) -> dict:
    """Worker 入口：执行完整工作流。返回 state；若含 __interrupt__ 表示挂起转人工。"""
    graph = compile_graph()
    config = {"configurable": {"thread_id": str(case_id)}}
    result = graph.invoke(
        {"case_id": case_id, "trace_id": trace_id},
        config,
    )
    if "__interrupt__" in result:
        logger.info("[%s] case %s 挂起等待人工审批", trace_id, case_id)
    else:
        logger.info("[%s] case %s 决策完成: %s", trace_id, case_id, result.get("decision"))
    return result


def resume_workflow(case_id: int, action: str, comment: str, operator: str) -> dict:
    """人工审批入口：从 interrupt 处恢复图继续执行（Phase 3 审批接口调用）。"""
    graph = compile_graph()
    config = {"configurable": {"thread_id": str(case_id)}}
    result = graph.invoke(
        Command(resume={"action": action, "comment": comment, "operator": operator}),
        config,
    )
    logger.info("case %s 人工审批 %s（%s）完成", case_id, action, operator)
    return result
