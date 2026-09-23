"""LangGraph 节点实现：Intake / Evidence / Fraud / Sentiment / Decision / HumanReview / Finalize。

每个节点：AgentRun 轨迹落库（大屏+审计）+ 事件发布 + 状态推进（乐观锁）。
日志不记录敏感内容（描述原文不打印）。
"""
import json
import logging
from datetime import datetime
from functools import wraps
from typing import Callable

from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt
from sqlalchemy import update

from app.agents.providers import (
    FakeMergedRiskProvider,
    FakeOcrProvider,
    MergedRiskProvider,
    OcrProvider,
)
from app.agents.vision import FakeVisionProvider, VisionProvider
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.domain.models import (
    AgentRun,
    AuditLog,
    CaseEvidence,
    Order,
    OrderStatus,
    RefundCase,
    ReviewTask,
    RiskAssessment,
)
from app.domain.status import CaseStatus
from app.infrastructure.events import publish_event
from app.infrastructure.storage import image_url_to_disk
from app.infrastructure.streams import get_redis
from app.workflow.assignment import pick_least_active
from app.policy.decision import (
    DECISION_APPROVE,
    DECISION_HUMAN_REVIEW,
    DECISION_REJECT,
    Decision,
    DecisionPolicy,
)
from app.workflow.order_verify import (
    DbOrderVerifyProvider,
    FakeOrderVerifyProvider,
    OrderVerifyProvider,
)
from app.workflow.state import RefundWorkflowState

# 工单6 安全网关（Critic 语义安检 + DLP 脱敏）；规则引擎零依赖，默认常驻
from app.security.critic import (
    CriticProvider,
    CriticResult,
    NoopCriticProvider,
    RuleBasedCriticProvider,
)
from app.security.dlp import DLPProvider, NoopDlpProvider, RuleBasedDlpProvider

logger = logging.getLogger("workflow")

settings = get_settings()

# Provider 注入点（Phase 6 替换为真实 PaddleOCR / LLM 实现；测试可 monkeypatch）
ocr_provider: OcrProvider = FakeOcrProvider()
# 视觉理解（Qwen2.5-VL via Ollama）：凭证图片语义描述 -> 风控一致性校验；
# 默认 Fake（演示确定性）；生产由 factory 注入 Ollama/Noop（Ollama 不可达静默跳过）。
vision_provider: VisionProvider = FakeVisionProvider()
# 工单5 成本优化移植：Fraud+Sentiment 两次 LLM 调用合并为一次（MergedRiskProvider）
merged_risk_provider: MergedRiskProvider = FakeMergedRiskProvider()
decision_policy: DecisionPolicy = DecisionPolicy()
# 订单三查（v2.0 §7/§8）：默认 Fake 放行（隔离单测不依赖真实订单）；
# 生产由 app.agents.factory.configure_providers 注入 DbOrderVerifyProvider 强制开启。
order_verify_provider: OrderVerifyProvider = FakeOrderVerifyProvider()

# 工单6 安全网关：Critic/DLP 默认规则引擎常驻（零依赖、主动防御）；
# use_fake_providers=true 时由 factory 切换 Noop（隔离测试不拦截/不脱敏）。
critic_provider: CriticProvider = RuleBasedCriticProvider()
dlp_provider: DLPProvider = RuleBasedDlpProvider()

# 工单8 双层意图识别：规则引擎常驻 + FakeLlm 降级（零依赖隔离测试 / 无 API key 降级）；
# use_fake_providers=False 时由 factory 注入真实 LlmIntentProvider(LLMClient)。
from app.intent import (
    HybridIntentProvider,
    IntentProvider,
    IntentType,
    UNKNOWN,
    apply_declared_claim,
)

intent_provider: IntentProvider = HybridIntentProvider()

# 工单8 异常兜底：意图识别失败事件入死信队列（Redis 降级，绝不阻断主链路）
from app.infrastructure.dead_letter import enqueue as dlq_enqueue


# ---------- 工具 ----------

def _db_update_status(case_id: int, new_status: str, *, reason: str | None = None,
                      extra: dict | None = None) -> bool:
    """乐观锁更新案件状态；返回是否成功（0 行 = 并发冲突/状态已被改）。"""
    db = SessionLocal()
    try:
        case = db.get(RefundCase, case_id)
        if case is None:
            return False
        values = {"status": new_status, "version": case.version + 1}
        if reason is not None:
            values["review_reason"] = reason
        if extra:
            values.update(extra)
        r = db.execute(
            update(RefundCase)
            .where(RefundCase.id == case_id, RefundCase.version == case.version)
            .values(**values)
        )
        db.commit()
        return r.rowcount == 1
    finally:
        db.close()


# 复盘输入白名单：记录节点实际处理的输入字段（不存 errors 等过程量，避免冗余/敏感）
_INPUT_STATE_KEYS = (
    "case_id", "amount_cent", "actual_amount_cent", "description", "refund_count",
    "evidence_text", "ocr_confidence", "evidence_status",
    "fraud_score", "fraud_features", "sentiment_score", "risk_level", "risk_reason",
    "decision", "review_reason",
)


def _summarize_input(state: RefundWorkflowState) -> dict:
    """从工作流 state 抽精选字段作节点输入快照（同 _safe_summary 截断敏感长文本）。

    工单6 DLP：description/evidence_text 落库前先脱敏，杜绝明文 PII 进 AgentRun 日志。
    """
    snap = {k: v for k, v in state.items() if k in _INPUT_STATE_KEYS}
    for key in ("description", "evidence_text"):
        if key in snap and isinstance(snap[key], str):
            snap[key] = dlp_provider.mask(snap[key])[:500]
    return snap


def record_agent_run(agent_name: str):
    """节点执行轨迹包装器：记录输入/输出摘要、耗时、错误标签。"""

    def decorator(fn: Callable):
        @wraps(fn)
        def wrapper(state: RefundWorkflowState) -> RefundWorkflowState:
            case_id = state["case_id"]
            trace_id = state.get("trace_id", "unknown")
            started = datetime.now()
            output = None
            error_tag = None
            try:
                output = fn(state)
                return output
            except GraphInterrupt:
                # 人工挂起（interrupt）不是节点失败：正常记录后向上抛给框架
                raise
            except Exception as e:  # noqa: BLE001
                error_tag = _classify_error(e)
                logger.error("[%s] %s 节点失败: %s", trace_id, agent_name, e)
                publish_event(case_id, "AGENT_FAILED", {"agent": agent_name, "error_tag": error_tag})
                raise
            finally:
                finished = datetime.now()
                duration_ms = int((finished - started).total_seconds() * 1000)
                # 工单5 可观测移植：Langfuse 节点 Trace（spool 落盘 + 后台线程 flush，非阻塞降级）
                try:
                    _report_node_trace(trace_id, case_id, agent_name, duration_ms, error_tag, output)
                except Exception:  # noqa: BLE001 - Trace 上报绝不阻塞工作流
                    logger.debug("Trace 上报异常（已降级）")
                db = SessionLocal()
                try:
                    db.add(
                        AgentRun(
                            case_id=case_id,
                            agent_name=agent_name,
                            status="FAILED" if error_tag else "SUCCESS",
                            input_json=_summarize_input(state),
                            output_json=_safe_summary(output),
                            error_tag=error_tag,
                            started_at=started,
                            finished_at=finished,
                            duration_ms=duration_ms,
                            # telemetry：节点内 LLM 调用的真实 token（Fake/规则路径为 NULL）
                            prompt_tokens=_pop_token_usage(output, "prompt_tokens"),
                            completion_tokens=_pop_token_usage(output, "completion_tokens"),
                        )
                    )
                    db.commit()
                finally:
                    db.close()
                if error_tag is None:
                    publish_event(case_id, "AGENT_FINISHED", {"agent": agent_name})

        return wrapper

    return decorator


def _pop_token_usage(output: dict | None, key: str) -> int | None:
    """从节点返回的 token_usage 里取指定计数；无/非法则 None（不写假数字）。"""
    if not isinstance(output, dict):
        return None
    usage = output.get("token_usage")
    if not isinstance(usage, dict):
        return None
    val = usage.get(key)
    return val if isinstance(val, int) and val >= 0 else None


def _emit_subrun(
    state: RefundWorkflowState, agent_name: str, fn: Callable[[], dict | None]
) -> None:
    """工单6 安全网关子步骤：单独落 AgentRun + 事件（不改 LangGraph 拓扑）。

    Critic 注入检测 / DLP 脱敏 的逻辑内联在 intake_node 内（并非独立图节点），
    但为了让前端时间线能单独呈现这两个环节，这里补写 AgentRun 轨迹，字段与
    record_agent_run 装饰器保持一致（含非阻塞的 Langfuse Trace 上报）。

    注意：fn 内部执行真实工作，以便拿到准确耗时；返回值只放非 PII 的元数据，
    绝不写入描述原文/脱敏文本（脱敏模块本身的存在意义就是不让明文进日志）。
    """
    case_id = state["case_id"]
    trace_id = state.get("trace_id", "unknown")
    started = datetime.now()
    output: dict | None = None
    error_tag: str | None = None
    try:
        output = fn()
        return output
    except Exception as e:  # noqa: BLE001
        error_tag = _classify_error(e)
        logger.error("[%s] %s 子步骤失败: %s", trace_id, agent_name, e)
        publish_event(case_id, "AGENT_FAILED", {"agent": agent_name, "error_tag": error_tag})
        raise
    finally:
        finished = datetime.now()
        duration_ms = int((finished - started).total_seconds() * 1000)
        try:
            _report_node_trace(trace_id, case_id, agent_name, duration_ms, error_tag, output)
        except Exception:  # noqa: BLE001 - Trace 上报绝不阻塞工作流
            logger.debug("Trace 上报异常（已降级）")
        db = SessionLocal()
        try:
            db.add(
                AgentRun(
                    case_id=case_id,
                    agent_name=agent_name,
                    status="FAILED" if error_tag else "SUCCESS",
                    input_json=_summarize_input(state),
                    output_json=_safe_summary(output),
                    error_tag=error_tag,
                    started_at=started,
                    finished_at=finished,
                    duration_ms=duration_ms,
                )
            )
            db.commit()
        finally:
            db.close()
        if error_tag is None:
            publish_event(case_id, "AGENT_FINISHED", {"agent": agent_name})


def _report_node_trace(
    trace_id: str,
    case_id: int,
    agent_name: str,
    duration_ms: int,
    error_tag: str | None,
    output: dict | None,
) -> None:
    """Langfuse 节点 Trace 上报（工单5 移植）：spool 落盘 + 后台线程 flush。"""
    from app.telemetry.trace_reporter import _sanitize_trace_id, get_reporter

    get_reporter().report_node_sync(
        trace_id=_sanitize_trace_id(trace_id),
        case_id=case_id,
        node=agent_name,
        duration_ms=duration_ms,
        metadata={"status": "FAILED" if error_tag else "SUCCESS", "error_tag": error_tag},
    )


def _classify_error(e: Exception) -> str:
    msg = str(e)
    if "OCR" in msg or "超时" in msg:
        return "OCR_TIMEOUT"
    if "JSON" in msg or "解析" in msg:
        return "AI_PARSE_ERROR"
    return "UNKNOWN"


def _safe_summary(output: dict | None) -> dict | None:
    """输出摘要：不写原始描述/OCR 全文（防敏感信息入库过大）。

    L-4 修复：截断前先过 DLP 脱敏 —— 与 _summarize_input 一致，
    evidence_text/description 的明文 PII（手机号、身份证）不进 AgentRun 日志。
    """
    if not output:
        return None
    safe = dict(output)
    # telemetry 专列已由 _pop_token_usage 取走，不再混进业务输出摘要
    safe.pop("token_usage", None)
    for key in ("description", "evidence_text"):
        if key in safe:
            safe[key] = dlp_provider.mask((safe[key] or ""))[:200]
    return safe


def _security_block_reason(risk_score: float | None) -> str:
    """安全网关拦截的统一挂起理由（intake / decision / human_review 共用）。

    为什么需要：review_reason 是共享槽位，intake 写入后会被 intent_node（意图兜底）
    或 order_verify_node（订单三查）覆写。而安全拦截是**最高优先级定性**，
    故由本函数统一自证文案——三条可能挂起的路径（decision 判定 / 意图兜底直跳 /
    订单三查定论）都能拿到与定性一致的理由，不依赖节点执行顺序。
    """
    return f"安全网关拦截：描述含越权/注入指令（风险分 {float(risk_score or 0.0):.2f}），转人工复核"


def _vision_fallback_reason(status: str) -> str:
    """VL 降级原因落库文案（前端分情况展示，排查不用猜）。

    VisionResult.status 值域：UNAVAILABLE（Ollama 不可达/未配置）/ TIMEOUT（推理超时）/
    PARSE_ERROR（输出非标准 JSON）；其余（如 OK 但描述为空）兜底统一文案。
    """
    return {
        "UNAVAILABLE": "（Ollama 不可用，已静默跳过视觉理解）",
        "TIMEOUT": "（视觉理解推理超时，已静默跳过）",
        "PARSE_ERROR": "（视觉模型输出无法解析，已静默跳过）",
    }.get(status, "（视觉理解不可用，已静默跳过）")


# ---------- 节点 ----------

@record_agent_run("INTAKE")
def intake_node(state: RefundWorkflowState) -> RefundWorkflowState:
    db = SessionLocal()
    try:
        case = db.get(RefundCase, state["case_id"])
        if case is None:
            raise ValueError("案件不存在")
        raw_desc = case.description or ""
    finally:
        db.close()

    # 工单6 安全网关：Critic 语义安检（先于下游 LLM，防注入劫持退款）
    critic_box: dict[str, CriticResult] = {}

    def _run_critic() -> dict:
        r = critic_provider.analyze(raw_desc)
        critic_box["r"] = r
        # matched 存的是命中的正则片段（非用户原文），可安全入审计
        return {
            "risk_score": r.risk_score,
            "action": r.action,
            "is_injection": r.is_injection,
            "is_jailbreak": r.is_jailbreak,
            "matched": r.matched,
        }

    _emit_subrun(state, "CRITIC", _run_critic)
    critic_result = critic_box["r"]

    # DLP 脱敏：PII 不进下游 LLM / 日志（工单6 合规红线）
    masked_box: dict[str, str] = {}

    def _run_dlp() -> dict:
        m = dlp_provider.mask(raw_desc)
        masked_box["m"] = m
        # 只记元数据，绝不落脱敏后文本（原文更不落）
        return {
            "masked_chars": len(m) if m else 0,
            "pii_hit": bool(m != raw_desc),
        }

    _emit_subrun(state, "DLP", _run_dlp)
    masked_desc = masked_box["m"]

    out: dict = {
        "amount_cent": case.applicant_amount,
        "actual_amount_cent": case.actual_amount,
        "description": masked_desc,
        "refund_count": 0,  # MVP：用户历史未建模，恒 0
        "errors": [],
        # 安全网关结果落 state，供 decision 路由 + 大屏/审计
        "security_risk_score": critic_result.risk_score,
        "security_action": critic_result.action,
    }
    if critic_result.blocked:
        out["security_matched"] = critic_result.matched
        out["review_reason"] = _security_block_reason(critic_result.risk_score)
    return out


@record_agent_run("INTENT")
def intent_node(state: RefundWorkflowState) -> RefundWorkflowState:
    """工单8 双层意图识别（PRD §4.1）：退货/换货/退款三意图 + 兜底转人工。

    规则层高置信直接采信（零 LLM）；否则 LLM 精判（喂脱敏文本合规）；LLM 故障/
    损坏 JSON/超时 → UNKNOWN → intent_fallback=True，由 graph 路由转人工复核 + 入死信队列
    （裁决 D-004：绝不因模型失败自动放行，不明意图也不默许自动退款）。

    识别结果同时落库 RefundCase（intent/intent_source/intent_confidence/intent_fallback），
    供前端大屏聚合与案件详情页读取（对齐 decision_node 的 db update 写法，非阻断）。
    """
    db = SessionLocal()
    try:
        case = db.get(RefundCase, state["case_id"])
        if case is None:
            raise ValueError("案件不存在")
        raw_desc = case.description or ""
        # 工单8 交叉校验：买家端显式三选一的售后类型（员工侧/旧案件为 NULL）
        claim_type = (case.claim_type or "").strip()
    finally:
        db.close()

    masked_desc = dlp_provider.mask(raw_desc)
    try:
        result = intent_provider.classify(raw_desc, masked=masked_desc)
        # 交叉校验：以用户显式选择为主通道，冲突转人工（详见 classifier.apply_declared_claim）
        result = apply_declared_claim(result, claim_type)
    except Exception as e:  # noqa: BLE001 - 双层任何异常，安全降级为兜底转人工
        logger.error("[%s] 意图识别异常，安全降级兜底转人工: %s", state.get("trace_id"), e)
        dlq_enqueue("intent", state["case_id"], {"masked": masked_desc[:200]}, str(e))
        out = {
            "intent": UNKNOWN,
            "intent_source": "fallback",
            "intent_confidence": 0.0,
            "intent_fallback": True,
            "review_reason": "意图识别异常，安全降级转人工复核",
            "token_usage": None,
        }
    else:
        needs_human = result.needs_human_review
        out = {
            "intent": str(result.intent),
            "intent_source": result.source,
            "intent_confidence": result.confidence,
            "intent_fallback": bool(needs_human),
            # telemetry：LLM 层真实 token（规则命中/兜底为 None）
            "token_usage": result.usage,
        }
        if needs_human:
            if result.declared_conflict:
                # 用户声明与文本识别冲突：review_reason 自带冲突说明，人工定夺
                out["review_reason"] = result.reason
            elif result.intent == IntentType.EXCHANGE:
                out["review_reason"] = "换货诉求转人工复核（MVP 不自动处理换货）"
            else:
                out["review_reason"] = f"意图不明（{result.reason}），转人工复核"
            logger.info("[%s] case %s 意图=%s 落 intent_fallback 转人工",
                        state.get("trace_id"), state["case_id"], result.intent)

    # 落库（非阻断：失败仅告警，不影响主链路路由）
    _persist_intent(state["case_id"], out)
    return out


def _persist_intent(case_id: int, out: dict) -> None:
    """意图识别结果写回 RefundCase（供前端读取）。非阻断降级。"""
    db = SessionLocal()
    try:
        db.execute(
            update(RefundCase)
            .where(RefundCase.id == case_id)
            .values(
                intent=out.get("intent"),
                intent_source=out.get("intent_source"),
                intent_confidence=out.get("intent_confidence"),
                intent_fallback=bool(out.get("intent_fallback", False)),
            )
        )
        db.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("case %s 意图结果落库失败（非阻断）: %s", case_id, e)
    finally:
        db.close()


@record_agent_run("ORDER_VERIFY")
def order_verify_node(state: RefundWorkflowState) -> RefundWorkflowState:
    """订单三查硬闸（v2.0 §7/§8）：伪造/重复订单直接拒，不一致转人工。

    命中 REJECT/REVIEW 时直接落定 decision，跳过下游 evidence/fraud/sentiment
    （脏数据不喂 OCR/LLM，既省钱又避免误判），由 decision 节点路由到对应出口。
    """
    db = SessionLocal()
    try:
        case = db.get(RefundCase, state["case_id"])
        if case is None:
            raise ValueError("案件不存在")
        result = order_verify_provider.verify(
            order_id=case.order_id,
            applicant_amount=case.applicant_amount,
            actual_amount=case.actual_amount,
            applicant_id=case.applicant_id,
            case_id=case.id,  # 防重复申请：排除当前案件，查同订单其他进行中案件
        )
    finally:
        db.close()

    out: dict = {
        "order_exists": result.order_exists,
        "product_matched": result.product_matched,
        "price_matched": result.price_matched,
        "already_refunded": result.already_refunded,
        "duplicate_case": result.duplicate_case,
        "order_verify_reason": result.reason,
        "order_verify_result": result.result,
    }
    if result.result in ("REJECT", "REVIEW"):
        out["decision"] = DECISION_REJECT if result.result == "REJECT" else DECISION_HUMAN_REVIEW
        out["review_reason"] = result.reason
    return out


@record_agent_run("EVIDENCE")
def evidence_node(state: RefundWorkflowState) -> RefundWorkflowState:
    db = SessionLocal()
    try:
        case = db.get(RefundCase, state["case_id"])
        evidence = (
            db.query(CaseEvidence).filter_by(case_id=case.id).first()
            if case else None
        )
    finally:
        db.close()

    if evidence is None or not evidence.image_url:
        return {"evidence_text": "", "ocr_confidence": None, "evidence_status": "EMPTY"}

    # L-5：image_url 落库为 URL 路径（/uploads/x 或旧相对 uploads/x），
    # OCR 内核按磁盘路径读取，统一归一化为 uploads 目录下的绝对路径。
    disk_path = image_url_to_disk(evidence.image_url)
    result = ocr_provider.extract(disk_path)

    # OCR 无字判定（有图但识别文本为空，如实物破损照片）：
    # 不阻断流程、OCR 部分不参与风控评分，仅作展示标记 NO_TEXT（见最终 return 注释）。
    # 排除 TIMEOUT：超时是异常态，仍走 TIMEOUT 转人工。
    no_text = (result.status != "TIMEOUT") and (
        result.status == "EMPTY" or not (result.text or "").strip()
    )

    # OCR 结果回写证据表（前端详情页展示识别文字与置信度）。
    # L-4 修复：落库前经 DLP 脱敏，收据/订单上的明文手机号、身份证等 PII
    # 不再以原文写入 case_evidence.ocr_text（可审计流水保留原文在内存 state 内，
    # 供风险模型判分；展示与存储一律用脱敏文本）。
    db = SessionLocal()
    try:
        ev = db.get(CaseEvidence, evidence.id)
        if ev:
            ev.ocr_text = dlp_provider.mask(result.text)
            ev.ocr_confidence = None if no_text else result.confidence
            ev.parse_status = "NO_TEXT" if no_text else result.status
            db.commit()
    finally:
        db.close()

    if result.status == "TIMEOUT":
        return {
            "evidence_text": "",
            "ocr_confidence": None,
            "evidence_status": "TIMEOUT",
            "errors": ["OCR_TIMEOUT"],
        }

    # ---- 视觉理解（Qwen2.5-VL）：图片语义描述 → 风控凭证一致性校验 ----
    # VL 输出可能携带图内文字（含 SN/IMEI/恶意指令），两道处理：
    #   ① 安全：description 先过 Critic 语义安检，BLOCK → 短路转人工、不喂下游模型；
    #   ② 隐私：落库前经 DLP 脱敏（图内可能印手机号/面单信息）。
    # vision_text 落库区分三种情况，前端分情况展示、排查不用猜：
    #   正常语义 -> 描述本身（脱敏）；Critic 拦截 / Ollama 不可用 / 推理超时 / 输出异常 -> 原因文案。
    # 结构化字段透传：is_damaged/severity/category/damage_type 进 state，供
    # 凭证一致性规则层做确定性比对（不止用 description 文本）。
    vision_description = ""
    vision_security_blocked = False
    vision_is_damaged: bool | None = None
    vision_severity = ""
    vision_category = ""
    vision_damage_type = ""
    vision_result = vision_provider.analyze(disk_path)
    if vision_result.available:
        critic = critic_provider.analyze(vision_result.description)
        if critic.blocked:
            vision_security_blocked = True
            vision_description = "（图片内容含潜在注入风险，已拦截，未提交给风险模型）"
            vision_persist = "（图片内容含潜在注入风险，已拦截）"
            # 拦截：不利用图内信息做一致性判定（图内可能携带注入指令）
        else:
            vision_description = vision_result.description
            vision_persist = dlp_provider.mask(vision_result.description)
            vision_is_damaged = vision_result.is_damaged
            vision_severity = vision_result.severity
            vision_category = vision_result.product_category
            vision_damage_type = vision_result.damage_type
    else:
        vision_persist = _vision_fallback_reason(vision_result.status)
    db = SessionLocal()
    try:
        ev = db.get(CaseEvidence, evidence.id)
        if ev:
            ev.vision_text = vision_persist
            db.commit()
    finally:
        db.close()

    # 识别文本为空（图内无可识别文字，如实物破损照片）-> 标记 NO_TEXT：
    # ① 不阻断流程、不转人工 —— OCR 只是"抽字"，抽不到字不代表凭证无效，
    #    语义理解交给 VL（对无文字图仍能描述破损），风控照常综合判断；
    # ② OCR 部分不参与风控评分 —— evidence_text 置空、置信度置 None，
    #    不给无意义的误检置信度（0.546 这类）进决策层；
    # ③ VL 描述（若可用）照常进入 state 与落库，供风控做"VL 语义 vs 用户描述"
    #    的一致性比对，也供前端详情页展示。
    return {
        "evidence_text": "" if no_text else result.text,
        "ocr_confidence": None if no_text else result.confidence,
        "evidence_status": "NO_TEXT" if no_text else result.status,
        "vision_description": vision_description,
        "vision_security_blocked": vision_security_blocked,
        # VL 结构化字段透传（凭证一致性规则层判定输入）
        "vision_is_damaged": vision_is_damaged,
        "vision_severity": vision_severity,
        "vision_category": vision_category,
        "vision_damage_type": vision_damage_type,
    }


@record_agent_run("FRAUD")
def fraud_node(state: RefundWorkflowState) -> RefundWorkflowState:
    """工单5 成本优化：风控+舆情合并为一次 LLM 调用。

    merged_risk_provider 单次输出 {fraud_score, fraud_features, sentiment_score, risk_level}，
    fraud_node 是合并调用点（Token ↓约 40~50%、时延砍半），sentiment_node 直接消费结果。
    """
    result = merged_risk_provider.assess(
        description=state.get("description", ""),
        evidence_text=state.get("evidence_text", ""),
        refund_count=state.get("refund_count", 0),
        vision_description=state.get("vision_description", ""),
    )
    # 凭证一致性独立判定（规则优先，LLM 兜底）：从 fraud_score 拆出的独立信号。
    # 规则层用 VL 结构化字段（is_damaged/severity）与描述做确定性比对（零 LLM、可解释）；
    # 规则判不了（UNCERTAIN）时才用合并调用里 LLM 的 evidence_consistent 兜底。
    from app.policy.evidence_consistency import evaluate_rule, merge_with_llm

    rule = evaluate_rule(
        description=state.get("description", ""),
        is_damaged=state.get("vision_is_damaged"),
        severity=state.get("vision_severity", ""),
    )
    cons = merge_with_llm(rule, result.evidence_consistent)
    return {
        "fraud_score": result.fraud_score,
        "fraud_features": result.fraud_features,
        "sentiment_score": result.sentiment_score,
        "risk_level": result.risk_level,
        # 工单5 锚定优化：LLM 打分理由一并写入 state，供追溯（AgentRun.output 可见）
        "risk_reason": result.reason,
        # LLM 兜底信号（consistent/uncertain/inconsistent）；penalty 恒 0（不污染风控分）
        "evidence_consistent": result.evidence_consistent,
        "evidence_penalty": result.evidence_penalty,
        # 凭证一致性独立信号（规则优先，LLM 兜底）：决策独立响应 + 前端独立展示
        "consistency_level": cons.level,
        "consistency_dimensions": cons.dimensions,
        "consistency_reason": cons.reason,
        "consistency_penalty": cons.penalty,
        # telemetry：真实 LLM token 用量（多采样已累加；规则路径 None），由装饰器落 AgentRun 专列
        "token_usage": result.usage,
    }


@record_agent_run("SENTIMENT")
def sentiment_node(state: RefundWorkflowState) -> RefundWorkflowState:
    """工单5 并行优化：风控+舆情已合并为一次调用，此节点零额外 LLM 调用。

    直接消费 fraud_node 写入的合并结果（LangGraph state 共享）；若确无结果
    （异常注入路径），以默认低风险兜底，绝不重复调用模型。
    """
    return {
        "sentiment_score": state.get("sentiment_score", 0.1),
        "risk_level": state.get("risk_level", "LOW"),
    }


@record_agent_run("DECISION")
def decision_node(state: RefundWorkflowState) -> RefundWorkflowState:
    # 工单6 安全网关已拦截（Critic BLOCK）：强制转人工复核，不跑风控/舆情阈值。
    # 已定决策①：拦截后转人工不直接拒绝，防误伤。
    if state.get("security_action") == "BLOCK":
        # 安全网关拦截是最高优先级定性。review_reason 是共享槽位，intake 写入后
        # 会被 intent_node（意图兜底）/ order_verify_node（订单三查）覆写，
        # 故此处不沿用 state 里的值，统一由 _security_block_reason 自证理由，
        # 保证人工审核员看到的挂起理由与实际定性一致（不依赖节点执行顺序）。
        d = Decision(
            DECISION_HUMAN_REVIEW,
            _security_block_reason(state.get("security_risk_score")),
        )
    # 订单三查已定论（伪造/重复→REJECT，不一致→HUMAN_REVIEW）：不再跑风控/舆情阈值
    elif state.get("order_verify_result") in ("REJECT", "REVIEW") and state.get("decision"):
        d = Decision(
            state["decision"],
            state.get("review_reason") or state.get("order_verify_reason", ""),
        )
    # 工单6 扩展：凭证图片 VL 描述命中 Critic（图内注入指令）→ 强制转人工复核
    elif state.get("vision_security_blocked"):
        d = Decision(
            DECISION_HUMAN_REVIEW,
            "凭证图片内容含潜在注入风险，已拦截并转人工复核",
        )
    # ④ 凭证一致性独立闸（从 fraud_score 拆出，规则优先，LLM 兜底）：
    #    MISMATCH + 高频退款 -> 直接拒（薅羊毛特征叠加，MVP 下 refund_count 恒 0 暂不触发）；
    #    MISMATCH / PARTIAL / UNCERTAIN -> 转人工（带不符维度原因，审核员看原图裁决）；
    #    描述与图片一致（MATCH / 无凭证）-> 放行到常规三段式。
    elif state.get("consistency_level") in ("MISMATCH", "PARTIAL", "UNCERTAIN"):
        level = state.get("consistency_level")
        dims = state.get("consistency_dimensions", [])
        dim_text = "；".join(dims) if dims else state.get("consistency_reason", "")
        if level == "MISMATCH" and state.get("refund_count", 0) >= 3:
            d = Decision(
                DECISION_REJECT,
                "声称损坏但凭证图片完好，且历史高频退款，疑似薅羊毛",
            )
        else:
            label = {
                "MISMATCH": "凭证与描述不符",
                "PARTIAL": "凭证与描述存在出入",
                "UNCERTAIN": "凭证信息不足",
            }.get(level, "凭证一致性存疑")
            d = Decision(
                DECISION_HUMAN_REVIEW,
                f"{label}（{dim_text}），转人工复核",
            )
    else:
        d = decision_policy.decide(
            amount_cent=state["amount_cent"],
            actual_amount_cent=state["actual_amount_cent"],
            ocr_confidence=state.get("ocr_confidence"),
            evidence_status=state.get("evidence_status", "OK"),
            fraud_score=state.get("fraud_score", 0.0),
            sentiment_score=state.get("sentiment_score", 0.0),
            errors=state.get("errors", []),
            order_exists=state.get("order_exists", True),
            product_matched=state.get("product_matched", True),
            price_matched=state.get("price_matched", True),
            already_refunded=state.get("already_refunded", False),
            order_verify_reason=state.get("order_verify_reason"),
        )
    # 决策与风险分落库（可审计）
    db = SessionLocal()
    try:
        db.execute(
            update(RefundCase)
            .where(RefundCase.id == state["case_id"])
            .values(
                fraud_score=state.get("fraud_score", 0.0),
                sentiment_score=state.get("sentiment_score", 0.0),
                risk_score=round(max(state.get("fraud_score", 0.0), state.get("sentiment_score", 0.0)), 3),
                decision=d.decision,
                review_reason=d.reason,
            )
        )
        db.add(
            RiskAssessment(
                case_id=state["case_id"],
                fraud_score=state.get("fraud_score", 0.0),
                sentiment_score=state.get("sentiment_score", 0.0),
                risk_level=state.get("risk_level", "LOW"),
                rule_version="decision-policy-v1",
                raw_json={"reason": d.reason},
            )
        )
        db.commit()
    finally:
        db.close()
    return {"decision": d.decision, "review_reason": d.reason}


@record_agent_run("HUMAN_REVIEW")
def human_review_node(state: RefundWorkflowState) -> RefundWorkflowState:
    """人工断点：首次进入落库 SUSPENDED + ReviewTask 并 interrupt 挂起；
    恢复后由 interrupt 返回注入值，更新 ReviewTask 并返回人工决策。"""
    case_id = state["case_id"]
    db = SessionLocal()
    try:
        case = db.get(RefundCase, case_id)
        # 健壮判断：状态尚未挂起则执行首次挂起落库（兼容直跑工作流/Worker 两种入口）
        first_enter = case.status != CaseStatus.SUSPENDED.value
    finally:
        db.close()

    if first_enter:
        # 安全拦截是最高优先级定性：意图兜底路径（intent_fallback）会绕过 decision_node
        # 直奔本节点，此时 review_reason 已被 intent_node 覆写，故需自证，
        # 与 decision 的 BLOCK 分支保持同一文案（_security_block_reason）。
        reason = (
            _security_block_reason(state.get("security_risk_score"))
            if state.get("security_action") == "BLOCK"
            else state.get("review_reason")
        )
        ok = _db_update_status(case_id, CaseStatus.SUSPENDED.value, reason=reason)
        if not ok:
            raise RuntimeError("挂起失败：案件状态已变更")
        db = SessionLocal()
        try:
            # D-011 Least Active 派单：分给当前 PENDING 任务最少的在线客服
            assignee = pick_least_active(db)
            db.add(
                ReviewTask(
                    case_id=case_id,
                    status="PENDING",
                    assignee_id=assignee,
                    idempotency_key=f"review-{state['trace_id']}",
                )
            )
            # D-011：RefundCase 同步分派（大屏/列表按 assignee 过滤）
            case = db.get(RefundCase, case_id)
            if case is not None:
                case.assignee_id = assignee
            db.add(
                AuditLog(
                    case_id=case_id,
                    from_status=CaseStatus.RUNNING.value,
                    to_status=CaseStatus.SUSPENDED.value,
                    operator="workflow",
                    idempotency_key=state.get("trace_id"),
                )
            )
            db.commit()
        finally:
            db.close()
        publish_event(case_id, "REVIEW_REQUIRED", {"reason": reason or ""})
        logger.info("[%s] case %s 挂起转人工", state.get("trace_id"), case_id)
        # 挂起快照双写（v2.0 §11）：state 序列化到 Redis，TTL 7 天，供前端详情/批量导出/运维排查
        _write_suspend_snapshot(case_id, state)

    # interrupt：首次挂起；恢复时返回注入值
    resume = interrupt({"case_id": case_id, "question": "是否批准退款？"})
    action = str(resume.get("action", "")).upper()
    if action not in (DECISION_APPROVE, DECISION_REJECT):
        raise ValueError(f"非法的人工决策: {action}")
    # 案件离开挂起态，清理 Redis 快照（PG checkpoint 仍是权威恢复来源）
    _delete_suspend_snapshot(case_id)

    # 更新 ReviewTask（status 用状态值 APPROVED/REJECTED，action 保留动作值）+ 审计
    db = SessionLocal()
    try:
        task = db.query(ReviewTask).filter_by(case_id=case_id).first()
        if task:
            task.status = (
                CaseStatus.APPROVED.value
                if action == DECISION_APPROVE
                else CaseStatus.REJECTED.value
            )
            task.action = action
            task.approver_id = resume.get("operator")
            task.comment = resume.get("comment", "")
        db.add(
            AuditLog(
                case_id=case_id,
                from_status=CaseStatus.SUSPENDED.value,
                to_status=CaseStatus.APPROVED.value if action == DECISION_APPROVE else CaseStatus.REJECTED.value,
                operator=resume.get("operator", "unknown"),
                idempotency_key=state.get("trace_id"),
            )
        )
        db.commit()
    finally:
        db.close()
    return {
        "human_action": action,
        "human_comment": resume.get("comment", ""),
        "human_operator": resume.get("operator", ""),
    }


def _force_refund_failed(case_id: int, reason: str) -> None:
    """退款失败兜底：确保案件离开 APPROVED，落到 REFUND_FAILED 可重试态。

    M-2 修复：approve 后若退款执行失败/返回异常，案件原来会永远卡在 APPROVED
    （语言上"已批准"，实际钱未退、下一环无人推进）。按状态机合法路径推进：
        APPROVED -> REFUNDING -> REFUND_FAILED
    若案件已处于 REFUNDING/REFUND_FAILED/FAILED（provider 已推进），则原样返回，
    不重复覆盖终态。迁移失败仅告警，不阻断工作流主链路。
    """
    db = SessionLocal()
    try:
        case = db.get(RefundCase, case_id)
        if case is None:
            return
        if case.status in (
            CaseStatus.REJECTED.value,
            CaseStatus.COMPLETED.value,
            CaseStatus.FAILED.value,
            CaseStatus.REFUND_FAILED.value,
        ):
            return  # 已是终态或已标记失败，无需兜底

        if case.status == CaseStatus.APPROVED.value:
            # APPROVED -> REFUNDING（合法初转）
            r = db.execute(
                update(RefundCase)
                .where(RefundCase.id == case_id, RefundCase.version == case.version)
                .values(status=CaseStatus.REFUNDING.value, version=case.version + 1)
            )
            if r.rowcount == 0:
                return  # 并发冲突，交给持锁方
            # 注意：SQLAlchemy execute(update) 默认 synchronize_session 会把 ORM 对象里的
            # version 也同步为新值；这里必须以 DB 为准重新读取，否则 +1 推算会错位双重递增。
            case = db.get(RefundCase, case_id)
            if case is None or case.status != CaseStatus.REFUNDING.value:
                return

        # REFUNDING -> REFUND_FAILED
        r = db.execute(
            update(RefundCase)
            .where(RefundCase.id == case_id, RefundCase.version == case.version,
                   RefundCase.status == CaseStatus.REFUNDING.value)
            .values(status=CaseStatus.REFUND_FAILED.value, version=case.version + 1,
                    review_reason=reason[:512])
        )
        if r.rowcount == 0:
            return
        db.add(
            AuditLog(
                case_id=case_id,
                from_status=CaseStatus.REFUNDING.value,
                to_status=CaseStatus.REFUND_FAILED.value,
                operator="refund-retry",
                idempotency_key=f"refund-fail:{case_id}",
            )
        )
        db.commit()
        publish_event(case_id, "STATUS_CHANGED", {"status": CaseStatus.REFUND_FAILED.value, "reason": reason})
        logger.warning("case %s 已落到 REFUND_FAILED（原因: %s），可重试/转人工", case_id, reason)
    except Exception as e:  # noqa: BLE001 - 兜底迁移失败不阻断工作流
        logger.warning("case %s 迁移 REFUND_FAILED 失败: %s", case_id, e)
    finally:
        db.close()


def _writeback_order_refunded(case_id: int) -> None:
    """退款成功后回写订单状态为 REFUNDED（v2.0 §9 回写闭环）。

    与退款动作同函数、就近落库；订单不存在（历史无订单案件）则安全跳过。
    失败仅告警，不阻断案件终态（补偿任务兜底，避免劈叉导致钱退了单没标记）。
    """
    db = SessionLocal()
    try:
        case = db.get(RefundCase, case_id)
        if case is None:
            return
        order = db.query(Order).filter_by(order_no=case.order_id).first()
        # 兼容入参为 Order 主键 id 的情况（前端 mapOrder 曾把后端自增 id 直接当 order_id 传；
        # 与 order_verify 的兜底保持一致，否则回写永远找不到订单，防重复退款失效）
        if order is None and case.order_id.strip().isdigit():
            order = db.get(Order, int(case.order_id))
        if order is None:
            return
        order.status = OrderStatus.REFUNDED.value
        order.refunded_at = datetime.now()
        db.commit()
    except Exception as e:  # noqa: BLE001 - 回写失败不阻断主链路
        logger.warning("case %s 回写订单状态失败（Phase 10 补偿任务兜底）: %s", case_id, e)
    finally:
        db.close()


def _order_already_refunded_elsewhere(case_id: int) -> bool:
    """防双退纵深（退款执行前最后一道闸）：同订单是否已被**其他案件**退款完成。

    命中任一即视为已退款：
    1. 订单状态已回写为 REFUNDED（另一案件退款成功写回）；
    2. 同订单存在其他 COMPLETED（退款完成）的案件。
    并发/历史数据绕过创建闸与三查闸时，靠这一道在 finalize 拦截，堵住"一个订单退两次款"。
    """
    db = SessionLocal()
    try:
        case = db.get(RefundCase, case_id)
        if case is None:
            return False
        order = db.query(Order).filter_by(order_no=case.order_id).first()
        if order is None and case.order_id.strip().isdigit():
            order = db.get(Order, int(case.order_id))
        if order is None:
            return False
        if order.status == OrderStatus.REFUNDED.value:
            return True
        other = (
            db.query(RefundCase.id)
            .filter(
                RefundCase.id != case_id,
                RefundCase.order_id.in_([order.order_no, str(order.id)]),
                RefundCase.status == CaseStatus.COMPLETED.value,
            )
            .first()
        )
        return other is not None
    finally:
        db.close()


# ---------- 挂起快照双写（v2.0 §11） ----------
# PG checkpoint 是权威恢复源；Redis 仅作展示/导出/运维排查的辅助镜像。
# 两条写都做了降级：Redis 不可用（网络抖动/容器重启）只告警，绝不阻断人工挂起主流程。
_SUSPEND_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 天


def _mask_snapshot_state(state: RefundWorkflowState) -> dict:
    """快照前脱敏：复制 state，对敏感长文本做 DLP 掩码，杜绝原始 OCR/描述明文进 Redis。

    PII 落盘治理：evidence_text 是原始 OCR（含手机号/身份证），description 虽已在
    入口脱敏，仍统一过 mask 兜底——mask 对已掩码串是幂等的，不会破坏既有掩码。
    与 _safe_summary / _summarize_input 共用同一组敏感 key。state 是 LangGraph 共享
    引用，不能原地改，故先浅拷贝再替换。
    """
    snapshot = dict(state)
    for key in ("description", "evidence_text"):
        if snapshot.get(key):
            snapshot[key] = dlp_provider.mask(str(snapshot[key]))
    return snapshot


def _write_suspend_snapshot(case_id: int, state: RefundWorkflowState) -> None:
    """挂起时把工作流 state 双写到 Redis（key=refund:suspend:{case_id}, TTL 7 天）。

    脱敏：写入前经 _mask_snapshot_state 掩码 description/evidence_text，Redis 不留明文 PII。
    降级策略：Redis 连接/序列化异常仅 warning 记录，不抛出——人工挂起是核心链路，
    不能因为辅助缓存抖动而失败。读取方须以 PG 为准，Redis 命中也需二次校验。
    """
    try:
        redis = get_redis()
        payload = json.dumps(_mask_snapshot_state(state), ensure_ascii=False, default=str)
        redis.set(f"refund:suspend:{case_id}", payload, ex=_SUSPEND_TTL_SECONDS)
        logger.info("[suspend-snapshot] case %s 已写入 Redis 快照（TTL 7d）", case_id)
    except Exception as e:  # noqa: BLE001 - 辅助缓存降级
        logger.warning("case %s 写入挂起快照失败（非阻断，PG 仍为权威源）: %s", case_id, e)


def _delete_suspend_snapshot(case_id: int) -> None:
    """案件离开挂起态（人工 APPROVE/REJECT 恢复）后清理 Redis 快照。

    降级策略同上：删除失败仅告警，不影响案件终态落库。
    """
    try:
        redis = get_redis()
        redis.delete(f"refund:suspend:{case_id}")
        logger.info("[suspend-snapshot] case %s 已清理 Redis 快照", case_id)
    except Exception as e:  # noqa: BLE001 - 辅助缓存降级
        logger.warning("case %s 清理挂起快照失败（非阻断）: %s", case_id, e)


@record_agent_run("FINALIZE")
def finalize_node(state: RefundWorkflowState) -> RefundWorkflowState:
    """终态落库：APPROVED 后执行 Mock 退款（-> REFUNDING -> COMPLETED）；REJECTED 终态。"""
    action = state.get("human_action") or state["decision"]
    case_id = state["case_id"]

    if action == DECISION_APPROVE:
        # 资损纵深（防"一个订单退两次款"）：退款执行前最后一道闸。
        # 同订单若已被其他案件退款完成（另一案 COMPLETED 或订单已 REFUNDED），本单拒绝。
        # 并发创建/历史数据绕过创建闸与三查闸时靠这一道兜底。
        if _order_already_refunded_elsewhere(case_id):
            reason = "该订单已有退款完成记录，不可重复退款"
            logger.warning("case %s 防双退拦截：同订单已有退款完成记录，拒绝本单", case_id)
            ok = _db_update_status(case_id, CaseStatus.REJECTED.value, reason=reason)
            if not ok:
                raise RuntimeError("防双退拦截后终态落库失败：案件状态已变更")
            publish_event(case_id, "STATUS_CHANGED", {"status": CaseStatus.REJECTED.value})
            return {"decision": action, "duplicate_refund_blocked": True}

        # 工单6 防御纵深：退款工具执行前再卡一道（Critic 漏拦的边缘注入）。
        # 经主管挂起态人工复核批准 -> 放行；否则若 description 仍含越权退款指令 -> 阻断。
        from app.security.tool_filter import filter_refund_action

        human_reviewed = bool(state.get("human_action"))
        blocked, block_reason = filter_refund_action(
            description=state.get("description", ""),
            human_reviewed=human_reviewed,
            decision=action,
        )
        if blocked:
            logger.warning("case %s 安全网关阻断退款：%s", case_id, block_reason)
            publish_event(case_id, "SECURITY_BLOCKED", {"reason": block_reason})
            ok = _db_update_status(case_id, CaseStatus.REJECTED.value, reason=block_reason)
            if not ok:
                raise RuntimeError("安全拦截后终态落库失败：案件状态已变更")
            publish_event(case_id, "STATUS_CHANGED", {"status": CaseStatus.REJECTED.value})
            return {
                "decision": action,
                "security_blocked": True,
                "security_block_reason": block_reason,
            }

        ok = _db_update_status(case_id, CaseStatus.APPROVED.value,
                               reason=state.get("review_reason"))
        if not ok:
            raise RuntimeError("终态落库失败：案件状态已变更")
        publish_event(case_id, "STATUS_CHANGED", {"status": CaseStatus.APPROVED.value})

        # 执行退款（Mock，独立幂等键防重复退款；真实网关 Phase 10 可替换）。
        # M-2 修复：退款失败必须推进案件状态机，绝不停留在 APPROVED（"已批准未退款"模糊态）。
        from app.infrastructure.idempotency import build_key
        from app.infrastructure.refund import MockRefundProvider

        refund_key = build_key("refund", str(case_id))
        try:
            result = MockRefundProvider().execute(
                case_id=case_id,
                amount_cent=state["amount_cent"],
                refund_key=refund_key,
            )
        except Exception as e:  # noqa: BLE001 - 退款引擎异常同样不得卡死 APPROVED
            logger.exception("case %s 退款执行异常: %s", case_id, e)
            _force_refund_failed(case_id, f"退款引擎异常: {e}")
            return {"decision": action}
        if not result.success:
            logger.warning("case %s 退款执行失败: %s（转入 REFUND_FAILED 可重试态）", case_id, result.message)
            # M-2：REFUNDING -> REFUND_FAILED（有界重试后仍失败 -> provider 内已落 FAILED 终态）。
            # 这里只是兜底：若 provider 提前返回（幂等命中/异常），确保案件不挂在 APPROVED。
            _force_refund_failed(case_id, result.message)
        else:
            # 回写闭环（v2.0 §9）：退款成功后同步订单状态，保证与退款状态一致，
            # 同时支撑「防重复退款」校验（再次申请会被 order_verify 拦截）
            _writeback_order_refunded(case_id)
    else:
        ok = _db_update_status(case_id, CaseStatus.REJECTED.value,
                               reason=state.get("review_reason"))
        if not ok:
            raise RuntimeError("终态落库失败：案件状态已变更")
        publish_event(case_id, "STATUS_CHANGED", {"status": CaseStatus.REJECTED.value})
    return {"decision": action}
