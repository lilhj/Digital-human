"""评测报告接口（LLM-as-a-judge 效果展示 + 工单8 周期测试）。

- GET  /api/v1/eval/report?real=false  读取最近一次评测报告（mock/real 双模式）
- POST /api/v1/eval/run                运行 Golden Dataset 评测并落盘（MANAGER 限定，real 调 LLM 裁判）
- GET  /api/v1/eval/periodic           读取最近一次周期测试报告（召回/幻觉/Token降幅 + 回归 + 红线）
- POST /api/v1/eval/periodic/run       一键触发周期测试（MANAGER 限定；save_baseline=true 同时固化为基线）
- GET  /api/v1/eval/rag                读取最近一次 RAG 客服评测报告（Recall/答案/拒答/路由/幻觉）
- POST /api/v1/eval/rag/run            一键触发 RAG 客服评测（MANAGER 限定；real=true 走 LLM 完整链路）

报告 JSON 结构见 harness.run_benchmark：{mode, aggregate, cases:[{id, scenario, kind,
expected, actual, match, reason, score:{correctness, safety, efficiency, reason, confidence}}]}
周期测试 JSON 结构见 periodic.run_periodic / load_periodic。
RAG 评测 JSON 结构见 rag_benchmark.run_rag_eval：{mode, aggregate, red_lines, cases:[{id, kind, pass, ...}]}
"""
import json
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException

from app.core.security import ROLE_MANAGER, get_current_user, require_roles
from app.core.config import get_settings
from app.domain.models import User
from app.eval import periodic as periodic_eval
from app.eval import rag_benchmark
from app.eval.harness import render_markdown, run_benchmark, save_results
from app.eval.report import md_file, report_file

router = APIRouter(prefix="/api/v1/eval", tags=["eval"])


class EvalRunBody(BaseModel):
    real: bool = False  # False=mock 离线确定性打分 / True=LLM-as-a-judge 真实裁判


class PeriodicRunBody(BaseModel):
    save_baseline: bool = False  # True=以本次结果为基线固化（供回归对比）


class RagEvalRunBody(BaseModel):
    real: bool = False  # False=mock 确定性原文路径 / True=走 LLM 完整链路（需 LLM_API_KEY）


def _load_report(real: bool) -> dict:
    """读取最近一次评测报告；未运行过则 404（前端据此引导先跑评测）。"""
    path = report_file(real)
    if not path.exists():
        mode = "真实裁判" if real else "离线"
        raise HTTPException(
            status_code=404,
            detail={"code": "EVAL_REPORT_NOT_FOUND", "message": f"尚无{mode}评测报告，请先运行评测"},
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    data["generated_at"] = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    return data


@router.get("/report")
def get_eval_report(
    user: Annotated[User, Depends(get_current_user)],
    real: bool = False,
):
    return _load_report(real)


@router.post("/run")
async def run_eval(
    body: EvalRunBody,
    user: Annotated[User, Depends(require_roles(ROLE_MANAGER))],
):
    """运行 Golden Dataset 评测并落盘报告（real 模式逐用例调 LLM 裁判，耗时约 1 分钟）。"""
    # real 模式前置校验：未配 key 时给 400 明确提示，而不是让 OpenAI SDK 的
    # Missing credentials 冒成 500 服务器内部错误（前端无法引导用户）。
    if body.real and not get_settings().llm_api_key:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "LLM_NOT_CONFIGURED",
                "message": "LLM_API_KEY 未配置，无法运行 LLM 真实裁判模式；请配置后重启后端，或改用离线打分模式",
            },
        )
    results = await run_benchmark(real=body.real)
    path = report_file(body.real)
    save_results(results, path)
    md_file(body.real).write_text(render_markdown(results), encoding="utf-8")
    results["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return results


# ---------------- 工单8 周期测试 ----------------

@router.get("/periodic")
def get_periodic_report(
    user: Annotated[User, Depends(get_current_user)],
):
    """读取最近一次周期测试报告；未跑过 -> ok:false（前端据此引导先一键触发）。"""
    return periodic_eval.load_periodic()


@router.post("/periodic/run")
def run_periodic_report(
    body: PeriodicRunBody,
    user: Annotated[User, Depends(require_roles(ROLE_MANAGER))],
):
    """一键触发周期测试：意图基准 + 决策 Golden（mock）+ 基线回归 + 红线判定，落盘 JSON+MD。

    save_baseline=true 时同时以本次结果为基线固化（供后续回归对比）。
    离线确定性基准，秒级完成。
    """
    return periodic_eval.run_periodic(save_baseline=body.save_baseline)


# ---------------- 买家侧 RAG 客服评测 ----------------

@router.get("/rag")
def get_rag_report(
    user: Annotated[User, Depends(get_current_user)],
):
    """读取最近一次 RAG 客服评测报告；未跑过 -> ok:false（前端据此引导先一键触发）。"""
    return rag_benchmark.load_rag_eval()


@router.post("/rag/run")
def run_rag_report(
    body: RagEvalRunBody,
    user: Annotated[User, Depends(require_roles(ROLE_MANAGER))],
):
    """一键触发 RAG 客服评测：20 条用例 × 5 维指标（Recall/答案/拒答/路由/幻觉），落盘 JSON+MD。

    real=true 走 LLM 完整链路生成答案（可检测真实幻觉）；默认 mock 确定性原文路径，
    秒级完成。RED 红线（拒答/路由零容忍）不达标时 red_lines.all_pass=false。
    """
    if body.real and not get_settings().llm_api_key:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "LLM_NOT_CONFIGURED",
                "message": "LLM_API_KEY 未配置，无法运行 RAG 真实链路评测；请配置后重启后端，或改用 mock 模式",
            },
        )
    return rag_benchmark.run_rag_eval(use_llm=body.real)