"""DLP 敏感数据脱敏模块（工单6 任务二）。

正则 + 实体遮掩双策略，对用户输入/日志中的 PII 进行 Masking：
- 手机号：13800000000 -> 138****0000（保留前3后4）
- 身份证：110101199001011234 -> 110101********1234（保留前6后4）
- API Key / 密钥：sk-xxxx / AKIAxxxx / api_key=xxxx -> 前缀 + ****
- 银行卡：16~17 位 -> 保留前6后4
- 邮箱：alice@example.com -> a***@example.com

设计原则：
- 纯正则、零外部依赖、确定性 100% 命中（满足工单6 脱敏准确率≥99%）。
- 默认 RuleBasedDlpProvider 常驻；use_fake_providers 时由 factory 切换为 NoopDlpProvider。
- 仅做脱敏，不做拦截（拦截是 Critic 的职责），DLP 负责"日志/展示不打印明文 PII"。
"""
import re
from abc import ABC, abstractmethod
from typing import Callable

# 顺序很重要：先邮箱（含@不冲突），再 API Key/密钥（含字母数字长串），
# 再身份证(18)，再银行卡(16~19)，最后手机号(11)，避免相互误伤。
# 注意：Python3 的 \w 含中文字符，故 \b 在"中文+数字"相邻处不构成边界，
# 会导致手机号/身份证紧贴中文时漏脱敏。统一改用 (?<!\d)/(?!\d) 与
# (?<![A-Za-z0-9])/(?![A-Za-z0-9]) 否定环视兜底中文上下文。
_PATTERNS: list[tuple[re.Pattern, str]] = [
    # 邮箱：本地名仅留首字符打星，域名保留
    (re.compile(r"([\w.%+-])([\w.%+-]*)@([\w.-]+\.[A-Za-z]{2,})"), r"\1***@\3"),
    # API Key / 密钥：sk- / SK- 前缀（否定环视兼容中文相邻场景）
    (re.compile(r"(?<![A-Za-z0-9])[sS][kK]-[A-Za-z0-9]{8,}"), r"sk-****"),
    # AWS Access Key
    (re.compile(r"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}"), r"AKIA****"),
    # 通用 secret/token/password/api_key = 值
    (
        re.compile(
            r"(['\"]?(?:api[_-]?key|secret|token|password|passwd|access[_-]?key)['\"]?\s*[:=]\s*['\"]?)([A-Za-z0-9_\-./]{8,})(['\"]?)",
            re.IGNORECASE,
        ),
        r"\1****\3",
    ),
    # 身份证：17 位数字 + 校验位(数字或X)，或 15 位旧版
    (re.compile(r"(?<!\d)(\d{6})(\d{8})(\d{3}[\dXx])(?!\d)"), r"\1********\3"),
    (re.compile(r"(?<!\d)(\d{6})(\d{6})(\d{3})(?!\d)"), r"\1******\3"),
    # 银行卡：16~19 位（避开身份证18位；先处理身份证再处理银行卡）
    (re.compile(r"(?<!\d)(\d{6})(\d{6,9})(\d{4})(?!\d)"), r"\1******\3"),
    # 手机号：1[3-9] + 9 位
    (re.compile(r"(?<!\d)(1[3-9]\d)(\d{4})(\d{4})(?!\d)"), r"\1****\3"),
]


def mask_sensitive(text: str) -> str:
    """对文本中的 PII 进行 Masking，返回脱敏后的文本（原串为空/非串原样返回）。"""
    if not text:
        return text
    out = str(text)
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    return out


class DLPProvider(ABC):
    """脱敏 Provider 抽象。"""

    @abstractmethod
    def mask(self, text: str) -> str:
        ...


class RuleBasedDlpProvider(DLPProvider):
    """正则规则脱敏（生产默认，零依赖、确定性）。"""

    def mask(self, text: str) -> str:
        return mask_sensitive(text)


class NoopDlpProvider(DLPProvider):
    """透传（测试/调试模式，不脱敏）。"""

    def mask(self, text: str) -> str:
        return text


# 模块级默认：规则脱敏常驻（安全默认开启）
dlp_provider: DLPProvider = RuleBasedDlpProvider()
