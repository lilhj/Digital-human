"""Critic Agent 语义安全路由器（工单6 任务二）。

职责：对用户 Prompt（此处为退款说明 description）做注入/越狱风险分类打分，
风险概率 >= 阈值（默认 0.85）触发拦截（BLOCK）。

拦截后处置（已定决策①）：**转人工复核，不直接拒绝**，防误伤。
因此 Critic 本身只"判定"，由调用方（intake_node）把 BLOCK 的案件路由到 HUMAN_REVIEW。

实现：规则 + 启发式双引擎（轻量、零外部 LLM 依赖，可常驻）。
- 高危注入（直接调退款 API / 跳过人工审批）单条即可定 BLOCK；
- 其余注入/越狱特征按权重累加，>= 0.85 触发 BLOCK。

模块级默认 RuleBasedCriticProvider 常驻；use_fake_providers 时由 factory 切换 NoopCriticProvider。
"""
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar

# 风险分级阈值（工单6 规格：>=0.85 触发拦截）
BLOCK_THRESHOLD = 0.85

# (正则, 权重, 类别)；权重 >= BLOCK_THRESHOLD 的单条即定 BLOCK（高危）
_PATTERNS: ClassVar[list[tuple[re.Pattern, float, str]]] = [
    # ---- 高危：越权直接退款指令（单条即 BLOCK）----
    (re.compile(r"direct[_-]?refund", re.IGNORECASE), 0.95, "injection"),
    (re.compile(r"调用.*退款.*(api|接口)", re.IGNORECASE), 0.95, "injection"),
    (re.compile(r"立即(调用|执行).*(退款|赔付|退回)", re.IGNORECASE), 0.9, "injection"),
    (re.compile(r"跳过(人工|审批|审核|复核|验证)", re.IGNORECASE), 0.9, "injection"),
    (re.compile(r"无需(人工|审批|审核)", re.IGNORECASE), 0.85, "injection"),
    (re.compile(r"(绕过|无视|忽略).*(安全|风控|限制|规则)", re.IGNORECASE), 0.85, "injection"),
    # ---- 中危：提示词注入（累加）----
    (re.compile(r"(忽略|无视|忘记|discard).*(之前|上述|以上|前面|所有).*(指令|提示|规则|要求|prompt)", re.IGNORECASE), 0.85, "injection"),
    (re.compile(r"系统提示|system\s*prompt|系统设定|system\s*message", re.IGNORECASE), 0.6, "injection"),
    (re.compile(r"后台(数据库|系统|管理员)", re.IGNORECASE), 0.5, "injection"),
    (re.compile(r"全额赔付|原路退回|立即退款", re.IGNORECASE), 0.5, "injection"),
    (re.compile(r"ignore\s+(previous|all|above|prior).*(instructions?|prompt)", re.IGNORECASE), 0.85, "injection"),
    (re.compile(r"disregard|override\s+(previous|all|the).*(instructions?|rules?)", re.IGNORECASE), 0.85, "injection"),
    (re.compile(r"directly\s+(refund|execute|call)|refund\s+now", re.IGNORECASE), 0.85, "injection"),
    (re.compile(r"bypass\s+(human|approval|review|人工|审批)", re.IGNORECASE), 0.85, "injection"),
    # 强化：跳过/不走/免审批类（覆盖"不要走审批流程""跳过所有限制"等变形）
    (re.compile(r"(跳过|不走|不要走|免|省略|规避|绕过).*(审批|审核|人工|风控|流程|限制|规则|验证)", re.IGNORECASE), 0.85, "injection"),
    # 强化：中文直接/立即/执行退款指令（覆盖"直接退款""执行退款"等）
    (re.compile(r"(直接|立即|执行).*(退款|赔付|退回)", re.IGNORECASE), 0.85, "injection"),
    (re.compile(r"base64.*(解码|解密).*(执行|运行|指令)", re.IGNORECASE), 0.7, "injection"),
    # 跨语言注入（日/西/韩高频词；规则引擎覆盖，保障多语言样本拦截率）
    (re.compile(r"(無視|システムプロンプト|直接返金|返金して|指示を無視)", re.IGNORECASE), 0.85, "injection"),
    (re.compile(r"(ignora|reembolsa|reembolso|instrucciones)", re.IGNORECASE), 0.85, "injection"),
    (re.compile(r"(무시하고|직접\s*환불|환불해)", re.IGNORECASE), 0.85, "injection"),
    # ---- 越狱 / 角色扮演（累加）----
    (re.compile(r"越狱|jailbreak|\bDAN\b|developer\s*mode", re.IGNORECASE), 0.45, "jailbreak"),
    (re.compile(r"没有任何(限制|约束)|no\s*(restrictions?|limits?)|without\s*(any\s*)?(restriction|limit)", re.IGNORECASE), 0.45, "jailbreak"),
    (re.compile(r"角色扮演|扮演|假装|假设你是|假设我|act\s*as|pretend|you\s*are\s*now", re.IGNORECASE), 0.4, "jailbreak"),
    (re.compile(r"不受(任何)?(约束|限制)|unconstrained", re.IGNORECASE), 0.45, "jailbreak"),
]


@dataclass
class CriticResult:
    """Critic 判定结果。"""

    risk_score: float
    is_injection: bool
    is_jailbreak: bool
    action: str  # "PASS" | "BLOCK"
    matched: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.action == "BLOCK"


class SecurityException(Exception):
    """Critic 拦截时抛出的安全异常（enforce 路径）。"""

    def __init__(self, result: CriticResult, message: str | None = None):
        self.result = result
        super().__init__(message or f"安全网关拦截：风险分 {result.risk_score:.2f}（{', '.join(result.matched)}）")


class CriticProvider(ABC):
    """Critic Provider 抽象。"""

    @abstractmethod
    def analyze(self, text: str) -> CriticResult:
        """返回风险判定结果（不抛异常）。"""

    def enforce(self, text: str) -> CriticResult:
        """判定并在 BLOCK 时抛出 SecurityException（截断恶意输入）。"""
        result = self.analyze(text)
        if result.blocked:
            raise SecurityException(result)
        return result


class RuleBasedCriticProvider(CriticProvider):
    """规则 + 启发式双引擎（生产默认）。"""

    def analyze(self, text: str) -> CriticResult:
        if not text:
            return CriticResult(0.0, False, False, "PASS", [])
        s = str(text)
        score = 0.0
        is_injection = False
        is_jailbreak = False
        matched: list[str] = []
        for pat, weight, category in _PATTERNS:
            if pat.search(s):
                matched.append(pat.pattern[:40])
                score += weight
                if category == "injection":
                    is_injection = True
                elif category == "jailbreak":
                    is_jailbreak = True
        score = min(1.0, score)
        action = "BLOCK" if score >= BLOCK_THRESHOLD else "PASS"
        return CriticResult(
            risk_score=round(score, 3),
            is_injection=is_injection,
            is_jailbreak=is_jailbreak,
            action=action,
            matched=matched,
        )


class NoopCriticProvider(CriticProvider):
    """透传（测试/调试模式，不拦截）。"""

    def analyze(self, text: str) -> CriticResult:
        return CriticResult(0.0, False, False, "PASS", [])


# 模块级默认：规则引擎常驻（安全默认开启）
critic_provider: CriticProvider = RuleBasedCriticProvider()
