"""客诉退赔决策系统 v2 · 沙箱隔离解析（工单5 移植）。

SANDBOX_MODE off/on 双模式：
- off：宿主机直读 Excel（漏洞基线，用于对照/无沙箱环境）
- on ：CubeSandbox(e2b 兼容) 隔离解析，默认断网 + 路径净化 + 关键字注入拦截
业务落点：批量审批 Excel 是「出系统 → 主管手动编辑 → 回流系统」的不可信输入，
必须隔离解析——这正是本模块的对抗目标。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_SANDBOX_TIMEOUT_S = 60.0


@dataclass
class SandboxResult:
    stdout: str = ""
    exit_code: int = 0


def sanitize_path(candidate: str, base: Path) -> Path:
    """路径净化：拒绝 .. 、绝对路径、符号链接逃逸。返回 base 下的 resolved 路径。"""
    base_res = base.resolve()
    if Path(candidate).is_absolute():
        raise ValueError(f"路径越界: {candidate}")
    p = (base_res / candidate).resolve()
    if base_res not in p.parents and p != base_res:
        raise ValueError(f"路径越界: {candidate}")
    return p


_TASK_ID_RE = r"^[A-Za-z0-9_-]{1,64}$"


def _validate_task_id(task_id: str) -> None:
    """task_id 格式校验：仅字母/数字/下划线/连字符（防路径穿越）。"""
    import re

    if not re.match(_TASK_ID_RE, task_id):
        raise ValueError(f"非法 task_id: {task_id!r}（仅允许字母/数字/_-，1-64 位）")


class SandboxAdapter:
    """CubeSandbox(e2b 兼容) 适配：建/执行/回收/断网/路径净化/逃逸拦截。"""

    def __init__(self, task_id: str, _sandbox_factory=None):
        cfg = get_settings()
        self.task_id = task_id
        self._sandbox = None
        self._factory = _sandbox_factory or self._default_factory
        self._semaphore = asyncio.Semaphore(4)  # 并发上限，防宿主 OOM

    def _default_factory(self):
        from e2b_code_interpreter import Sandbox

        cfg = get_settings()
        # e2b SDK 只从「进程环境变量」读 E2B_API_URL/E2B_API_KEY/E2B_SANDBOX_URL，
        # 而 .env 经 pydantic-settings 只进 Settings、不会导出到 os.environ——
        # 裸跑 uvicorn 时 SDK 看不到这些值会连默认云 API 而失败。
        # 这里在创建沙箱前显式注入（setdefault：用户已手动 export 时以环境为准），
        # 让 SANDBOX_MODE=on 只需 .env 配置即可生效（本地 CubeSandbox 实测）。
        if cfg.e2b_api_url:
            os.environ.setdefault("E2B_API_URL", cfg.e2b_api_url)
        if cfg.e2b_api_key:
            os.environ.setdefault("E2B_API_KEY", cfg.e2b_api_key)
        if cfg.e2b_sandbox_url:
            os.environ.setdefault("E2B_SANDBOX_URL", cfg.e2b_sandbox_url)
        return Sandbox.create(
            template=cfg.cube_template_id,
            allow_internet_access=False,  # 红线：默认断网
        )

    async def create(self) -> None:
        self._sandbox = await asyncio.to_thread(self._factory)
        self._apply_sandbox_url_override()

    def _apply_sandbox_url_override(self) -> None:
        """本地 CubeSandbox：执行端点改为 cube-proxy 路径路由（Windows 宿主实测可用）。

        SDK 默认执行 URL 是 https://<id>.cube.app（*.cube.app 仅 VM 内 DNS 可解析），
        本地部署可经 proxy 的 http 映射走路径式路由 /sandbox/<id>/49999/，无需改 hosts/证书。
        """
        cfg = get_settings()
        tmpl = cfg.e2b_sandbox_url
        if not tmpl:
            return
        sb = self._sandbox
        cc = getattr(sb, "connection_config", None)
        sid = getattr(sb, "sandbox_id", "")
        url = tmpl.format(sandbox_id=sid)
        if cc is not None:
            cc._sandbox_url = url
        for obj in (
            sb,
            getattr(sb, "_filesystem", None),
            getattr(sb, "_commands", None),
            getattr(sb, "_pty", None),
        ):
            api = getattr(obj, "_envd_api", None) if obj is not None else None
            if api is not None and hasattr(api, "base_url"):
                api.base_url = url
        for name in ("_filesystem", "_commands", "_pty"):
            sub = getattr(sb, name, None)
            if sub is not None and hasattr(sub, "_envd_api_url"):
                sub._envd_api_url = url
        for mangled in ("_Sandbox__envd_api_url", "_Sandbox__envd_direct_url"):
            if hasattr(sb, mangled):
                setattr(sb, mangled, url)

    async def run_python(self, code: str, env: dict | None = None) -> SandboxResult:
        if self._sandbox is None:
            await self.create()
        async with self._semaphore:
            kwargs = {"envs": env} if env else {}
            result = await asyncio.wait_for(
                asyncio.to_thread(self._sandbox.run_code, code, **kwargs),
                timeout=_SANDBOX_TIMEOUT_S,
            )
            out = "\n".join(getattr(result.logs, "stdout", []) or [])
            if getattr(result, "error", None):
                return SandboxResult(stdout=out, exit_code=1)
            return SandboxResult(stdout=out, exit_code=getattr(result, "exit_code", 0))

    async def write_remote(self, remote: str, data: bytes) -> None:
        """写字节到沙箱远端文件：run_code + base64（不用 files API）。

        本地 CubeSandbox 的 cube-proxy 对 /files 路径路由返回 404，而 /execute 可用。
        """
        if self._sandbox is None:
            await self.create()
        data_b64 = __import__("base64").b64encode(data).decode()
        code = (
            "import base64, json\n"
            f"path = {json.dumps(remote)}\n"
            f"raw = base64.b64decode({json.dumps(data_b64)})\n"
            "open(path, 'wb').write(raw)\n"
        )
        res = await self.run_python(code)
        if res.exit_code != 0:
            raise RuntimeError(f"沙箱写入 {remote} 失败: {res.stdout or 'NO_OUTPUT'}")

    async def upload(self, local: Path, remote: str) -> None:
        if self._sandbox is None:
            await self.create()
        await self.write_remote(remote, local.read_bytes())

    async def destroy(self) -> None:
        """幂等销毁：正常/异常/超时都必须调用。"""
        if self._sandbox is not None:
            await asyncio.to_thread(self._sandbox.kill)
            self._sandbox = None
            logger.info("沙箱 %s 已销毁", self.task_id)


# ---- 逃逸拦截（轻量关键字提示，安全完全依赖沙箱底层隔离 + 断网）----

_BLOCKED_KEYWORDS = (
    "os.system",
    "subprocess",
    "popen",
    "shell=True",
    "eval(",
    "exec(",
    "win.ini",
    "C:",
    "D:",
    "/etc/",
    "tail /etc",
    "surprise",
    "curl",
    "wget",
    "http://",
    "https://",
    "socket",
    "base64 -d",
    "/dev/",
)


def guard_escape_attempt(user_input: str) -> bool:
    """注入拦截判定：返回 True 表示放行，False 表示拦截。"""
    lowered = user_input.lower()
    if any(k in lowered for k in _BLOCKED_KEYWORDS):
        return False
    if user_input.strip().startswith(("运行", "执行", "帮我运行")):
        if any(k in lowered for k in ("python", "import", "os.", "print(", "代码")):
            return False
    return True


# ---- 批量审批 Excel 解析（业务落点）----

# 审批动作枚举（需求文档 §3.1：固定枚举 同意/拒绝）
APPROVE_ACTION = "APPROVE"
REJECT_ACTION = "REJECT"
# 中文 key 原样；英文 key 统一小写（_normalize_action 已 casefold）
_ACTION_MAP = {
    "同意": APPROVE_ACTION,
    "拒绝": REJECT_ACTION,
    "approve": APPROVE_ACTION,
    "reject": REJECT_ACTION,
    "approval": APPROVE_ACTION,   # 容错：approve 的名词形式
    "deny": REJECT_ACTION,        # 容错：reject 的另一个词
}


def _normalize_action(action_raw: str | None) -> str:
    """审批动作归一：全空白压缩（含全角空格→普通空格）+ 小写，再查表。

    主管手输「同 意」「APPROVE身边带空格」「approval」等都能命中，杜绝
    「非法审批动作: ''」（bug 根因：导出 Excel 动作留空）。返回空串表示无法识别。
    """
    if action_raw is None:
        return ""
    text = str(action_raw)
    # 全角空格(　) + 不间断空格( ) 也视为空白删除，避免归一后残留空格导致查表失败
    return "".join(text.split()).casefold()


def parse_approval_excel_local(path: Path) -> list[dict]:
    """SANDBOX_MODE=off：宿主机直读解析 Excel（漏洞基线，仅对照用）。"""
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True)
    rows: list[dict] = []
    for ws in wb.worksheets:
        header = None
        for row in ws.iter_rows(values_only=True):
            if header is None:
                header = [str(c or "").strip() for c in row]
                continue
            record = dict(zip(header, row))
            rows.append(record)
    return _normalize_rows(rows)


def parse_approval_excel_sandbox(path: Path, task_id: str = "batch") -> list[dict]:
    """SANDBOX_MODE=on 的同步入口：CubeSandbox 隔离解析 Excel（不可信输入必须隔离）。

    流程：上传 xlsx → 沙箱内 openpyxl 解析 → JSON 回传 → 宿主仅取结构化记录。
    内部用 asyncio.run 驱动 async 实现；批量服务在独立线程池执行，安全。
    """
    return asyncio.run(parse_approval_excel_sandbox_async(path, task_id))


async def parse_approval_excel_sandbox_async(path: Path, task_id: str = "batch") -> list[dict]:
    """SANDBOX_MODE=on 的 async 实现：沙箱隔离解析 Excel。"""
    _validate_task_id(task_id)
    if not path.exists():
        raise FileNotFoundError(f"待解析 Excel 不存在: {path}")
    sb = SandboxAdapter(task_id=task_id)
    try:
        await sb.create()
        remote = f"/home/user/{path.name}"
        await sb.upload(path, remote)
        code = (
            "import os, json\n"
            f"path = {json.dumps(remote)}\n"
            "from openpyxl import load_workbook\n"
            "wb = load_workbook(path, data_only=True)\n"
            "out = []\n"
            "for ws in wb.worksheets:\n"
            "    header = None\n"
            "    for row in ws.iter_rows(values_only=True):\n"
            "        if header is None:\n"
            "            header = ['' if c is None else str(c).strip() for c in row]\n"
            "            continue\n"
            "        rec = {h: ('' if c is None else c) for h, c in zip(header, row)}\n"
            "        out.append(rec)\n"
            "print(json.dumps({'status':'ok','rows':out}, ensure_ascii=False))\n"
        )
        res = await sb.run_python(code)
        stdout = res.stdout.strip()
        if res.exit_code != 0 or "{" not in stdout:
            raise RuntimeError(f"沙箱解析失败: {stdout or 'NO_OUTPUT'}")
        data = json.loads(stdout[stdout.find("{") : stdout.rfind("}") + 1])
        if data.get("status") != "ok":
            raise RuntimeError(f"沙箱解析失败: {data}")
        return _normalize_rows(data.get("rows", []))
    finally:
        await sb.destroy()


def _normalize_rows(rows: list[dict]) -> list[dict]:
    """列名归一 + 动作映射 + 校验：只保留含 case_id/动作的记录。

    表头容错：导出模板列名是一行中文标签（如「案件ID」「审批动作(同意/拒绝)」「审批意见」），
    主管也可能用英文列名（case_id/action/comment）。这里做「精确优先 + 包含匹配」的归一，
    避免导出模板带括号提示导致动作列读成空（bug 修复：SUSPENDED 单无法批量审批）。
    """
    normalized: list[dict] = []
    for raw in rows:
        if not raw:
            continue
        rec = {str(k).strip().lower(): v for k, v in raw.items() if k}

        # 表头归一：候选顺序即优先级；对每个候选先精确匹配，再包含匹配
        # （兼容「案件ID」「审批动作(同意/拒绝)」等导出模板中文标签）
        def _pick(candidates: list[str]) -> object:
            for cand in candidates:
                if cand in rec:
                    return rec[cand]
            for cand in candidates:
                for k, v in rec.items():
                    if cand in k:
                        return v
            return None

        case_id = _pick(["case_id", "案件id", "工单号", "ticket_no"])
        action_raw = _pick(["审批动作", "动作", "action"])
        comment = _pick(["审批意见", "comment"]) or ""
        if case_id is None or str(case_id).strip() in ("", "none"):
            continue  # 空行
        # 全空白归一 + 小写查表，兼容手输全角空格/大小写变体（如「APPROVE」带空格）
        normalized_action = _normalize_action(action_raw)
        action = _ACTION_MAP.get(normalized_action) if normalized_action else None
        item = {
            # case_id 可能是数字案件ID，也可能是工单号（T2026...）；ticket_no 单独保留兜底
            "case_id": str(case_id).strip(),
            "ticket_no": str(_pick(["ticket_no", "工单号"])) if _pick(["ticket_no", "工单号"]) is not None else str(case_id).strip(),
            "action": action,
            "action_raw": str(action_raw or "").strip(),
            "comment": str(comment or "").strip(),
            "order_id": str(_pick(["订单号", "order_id"]) or "").strip(),
            "amount": _pick(["金额", "applicant_amount"]),
            "risk_score": _pick(["风险分", "risk_score"]),
            "sentiment": _pick(["舆情等级", "sentiment_level"]),
        }
        if action is None:
            item["error"] = f"非法审批动作: {item['action_raw']!r}"
        normalized.append(item)
    return normalized
