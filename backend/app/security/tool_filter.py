"""Tool-calling 过滤（工单6 任务二）：退款工具执行前的防御纵深。

Critic 已在 intake 处把高风险案件路由到 HUMAN_REVIEW（转人工，不自动批准）。
Tool 过滤是**最后一道闸**：在 finalize 真正执行退款（MockRefundProvider / 真实退款 API）
之前，再校验一次"批准动作是否可能被注入驱动"。

拦截条件（防御纵深，非主拦截）：
- 决策为 APPROVE（要退款）；
- 且未经人工复核（human_reviewed=False，即非主管在挂起态批准）；
- 且 description 仍含越权退款指令（Critic 漏拦或边缘命中）。

命中则阻断退款并返回原因，由 finalize 落"拦截"终态（不退款、交人工/审计），
杜绝"大模型被劫持直接调退款 API"的资损事件（工单6 背景案例）。
"""
import re

# 越权退款指令（高危，单条即危险）。与 critic 高危档一致。
_DANGEROUS_DIRECTIVE = re.compile(
    r"(direct[_-]?refund|调用.*退款.*(api|接口)|立即(调用|执行).*(退款|赔付|退回)|"
    r"跳过(人工|审批|审核|复核)|无需(人工|审批)|base64.*(解码|解密).*(执行|运行))",
    re.IGNORECASE,
)


def contains_dangerous_directive(text: str) -> bool:
    """description 是否含越权退款指令（供 Tool 过滤与测试复用）。"""
    if not text:
        return False
    return bool(_DANGEROUS_DIRECTIVE.search(str(text)))


def filter_refund_action(
    *,
    description: str,
    human_reviewed: bool,
    decision: str,
) -> tuple[bool, str]:
    """退款工具执行前校验。

    返回 (blocked, reason)：
    - blocked=False 且 reason="" -> 放行；
    - blocked=True -> 阻断退款，reason 说明原因。
    """
    if decision != "APPROVE":
        return (False, "")
    # 经主管在挂起态人工复核批准 -> 放行（人工已背书）
    if human_reviewed:
        return (False, "")
    # 未经人工复核却要退款：若 description 含越权指令则阻断（防御纵深）
    if contains_dangerous_directive(description):
        return (
            True,
            "安全网关拦截：检测到注入驱动的越权退款指令，且未经人工复核，已阻断退款",
        )
    return (False, "")
