"""凭证一致性独立判定（规则优先，LLM 兜底）。

从 fraud_score 中拆出的独立信号：只回答「用户的描述声明 与 凭证图片语义 是否对得上」。
一致性惩罚不再叠加进风控分，而是独立成 level + penalty + 不符维度，供决策独立响应、
前端独立展示（可解释、可审计）。

档位：
- MATCH      描述声明与图片语义一致 -> 0 惩罚，放行到常规风控三段式
- PARTIAL    图片有损伤但描述未主张 / 描述与图有出入（非薅羊毛方向）-> +15 分
- MISMATCH   声称损坏但图片完好（薅羊毛强信号）-> +30 分
- UNCERTAIN  VL 不可用 / 描述含糊无状态声明 / 图片无法判断 -> 0 分（信息不足转人工）

规则层优先（零 LLM、确定性、可审计）；规则层判 UNCERTAIN 且 LLM 有兜底判定时，
用合并风控调用里的 evidence_consistent 兜底（不新增调用）。

设计背景（工单 T202609101933114440C7 修复链）：
- 之前一致性由 LLM 在 fraud 调用里输出 + 直接叠进 fraud_score（+30），
  导致「凭证比描述更严重但描述含糊」被误顶高风险、且信号不可解释。
- 现在：fraud_score 纯净化，一致性独立；「声称损坏但图完好」是唯一 MISMATCH 强信号。
"""
from dataclasses import dataclass, field

# 复用 providers 的破损关键词与含糊判定（避免词表两处维护）
from app.agents.providers import _DAMAGE_KEYWORDS, _description_is_vague

# 状态声明常量
MATCH = "MATCH"
PARTIAL = "PARTIAL"
MISMATCH = "MISMATCH"
UNCERTAIN = "UNCERTAIN"

# 完好声明词（描述明确说商品没问题）
_INTACT_KEYWORDS = (
    "完好", "无损", "没问题", "无问题", "没坏", "没碎", "没破", "无破损",
    "正常", "好的", "没毛病", "完好无损",
)

# 强损伤词（描述夸大损伤用；与 VL severity="轻微" 组合触发 PARTIAL）
_SEVERE_KEYWORDS = (
    "粉碎", "报废", "炸裂", "碎成渣", "彻底坏", "完全坏", "完全碎", "严重",
)

# 各档位展示用的惩罚分（独立展示字段，不叠加进 fraud_score）
PENALTY = {MATCH: 0.0, PARTIAL: 0.15, MISMATCH: 0.30, UNCERTAIN: 0.0}


@dataclass
class EvidenceConsistencyResult:
    level: str = UNCERTAIN               # MATCH / PARTIAL / MISMATCH / UNCERTAIN
    penalty: float = 0.0                 # 展示用惩罚分（+15/+30），不参与 fraud_score
    dimensions: list[str] = field(default_factory=list)  # 不符维度（可解释、可展示）
    reason: str = ""
    source: str = "rule"                 # rule / llm / none


def penalty_of(level: str) -> float:
    return PENALTY.get(level, 0.0)


def evaluate_rule(
    *,
    description: str,
    is_damaged: bool | None,
    severity: str = "",
) -> EvidenceConsistencyResult:
    """规则层三维比对（破损声明 / 严重度夸大），确定性、零 LLM。

    入参为 VL 结构化字段（is_damaged / severity）。返回规则层判定；
    无法判定（UNCERTAIN）时由上层用 LLM evidence_consistent 兜底。
    """
    dims: list[str] = []
    desc = (description or "").strip()

    # ---- 维度① 破损声明（主维度）----
    claims_damage = any(k in desc for k in _DAMAGE_KEYWORDS)
    claims_intact = any(k in desc for k in _INTACT_KEYWORDS)

    if is_damaged is None:
        level = UNCERTAIN  # VL 未给出图片损坏状态，交给 LLM 兜底
    elif claims_damage and is_damaged:
        level = MATCH       # 声称损坏 + 图片确实损坏
    elif claims_damage and not is_damaged:
        level = MISMATCH    # 声称损坏 + 图片完好：薅羊毛强信号
        dims.append("破损声明矛盾（描述称损坏但图片显示完好）")
    elif claims_intact and not is_damaged:
        level = MATCH       # 都说完好（"商品完好，全额退款"归 fraud 判，一致性一致）
    elif claims_intact and is_damaged:
        level = PARTIAL     # 说完好但图片有损（图比描述严重，非薅羊毛方向）
        dims.append("破损声明出入（描述称完好但图片显示有损伤）")
    elif is_damaged:
        level = PARTIAL     # 图片有损但描述无状态主张，人工确认诉求
        dims.append("图片显示损伤但描述未主张")
    else:
        level = UNCERTAIN   # 图片完好 + 描述无状态声明，无法对照

    # ---- 维度② 严重度夸大（轻量，只在描述含强损伤词且 VL 判定轻微时）----
    if severity and "轻微" in severity and any(k in desc for k in _SEVERE_KEYWORDS):
        level = PARTIAL if level == MATCH else level
        if "损伤描述可能夸大（描述严重但图片轻微）" not in dims:
            dims.append("损伤描述可能夸大（描述严重但图片轻微）")

    penalty = penalty_of(level)
    return EvidenceConsistencyResult(
        level=level,
        penalty=penalty,
        dimensions=dims,
        reason=_rule_reason(level, dims),
        source="rule",
    )


def _rule_reason(level: str, dims: list[str]) -> str:
    if dims:
        return "；".join(dims)
    return {
        MATCH: "描述声明与凭证图片语义一致",
        PARTIAL: "描述与凭证图片存在出入",
        MISMATCH: "描述声称损坏但凭证图片显示完好",
        UNCERTAIN: "凭证信息不足或描述无明确状态声明",
    }.get(level, "")


def merge_with_llm(
    rule: EvidenceConsistencyResult,
    llm_consistent: str | None,
) -> EvidenceConsistencyResult:
    """规则优先，规则判不了（UNCERTAIN）时用 LLM evidence_consistent 兜底。

    LLM 值域 consistent/uncertain/inconsistent -> MATCH/UNCERTAIN/MISMATCH。
    注意：规则已判 PARTIAL/MISMATCH/MATCH 时 LLM 不改判（规则是确定性权威，
    LLM 仅兜底"信息不足"场景，避免 LLM 误判再污染）。
    """
    if rule.level != UNCERTAIN:
        return rule
    if not llm_consistent:
        return rule
    llm = llm_consistent.strip().lower()
    if llm == "consistent":
        return EvidenceConsistencyResult(MATCH, 0.0, [], "LLM 兜底：凭证佐证描述", "llm")
    if llm == "inconsistent":
        return EvidenceConsistencyResult(
            MISMATCH, penalty_of(MISMATCH), ["LLM 判定描述与凭证不符"],
            "LLM 兜底：描述与凭证不符", "llm",
        )
    return rule  # uncertain 或无意义 -> 保持 UNCERTAIN
