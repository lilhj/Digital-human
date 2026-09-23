"""全局配置：所有配置从环境变量读取（pydantic-settings）。"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env 固定指向项目根目录（与 CWD 无关，保证任意目录启动都能加载）
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # 基础
    app_name: str = "多Agent协同客诉舆情退赔决策系统"
    env: str = "dev"
    debug: bool = True

    # 数据库（Phase 0 C-1：Docker 容器 postgres:16）
    database_url: str = "postgresql+psycopg://refund:refund_dev_pw@localhost:5432/refund_cases"

    # Redis（队列/锁/事件）
    redis_url: str = "redis://localhost:6379/0"

    # JWT（>=32 字节，满足 RFC 7518 推荐强度）
    jwt_secret: str = "dev-insecure-secret-change-me-0123456789"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480

    # CORS（前端开发服务器）
    cors_origins: str = "http://localhost:5173"

    # 业务阈值（裁决 D-001/D-002/D-003；金额单位为分）
    amount_limit_cent: int = 30_000            # > 300 元强制人工
    ocr_confidence_auto: float = 0.7           # >= 0.7 进入自动决策
    ocr_confidence_review: float = 0.5         # 0.5~0.7 人工复核；< 0.5 强制人工
    risk_auto_max: float = 0.2                 # 风险分 <= 0.2 自动通过
    risk_review_max: float = 0.5               # 0.2~0.5 人工复核；> 0.5 拒绝
    max_active_tasks: int = 3                  # 单客服并发上限（裁决 D-011）
    suspend_ttl_hours: int = 48                # 挂起 TTL 兜底（裁决 D-013）
    refund_max_retries: int = 3                # 退款重试上限（裁决 D-010）
    approval_lock_ttl_ms: int = 10_000         # 审批锁 TTL（Loop Phase 7 规范）
    approval_lock_prefix: str = "refund:approval"

    # LLM（裁决 C-5：agicto.cn API；测试环境用 FakeProvider 不依赖真实模型）
    llm_base_url: str = "https://api.agicto.cn/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-v3"
    llm_timeout_seconds: int = 30
    llm_max_retries: int = 2
    # 场景二联调开关（Loop Phase 9："使用测试配置返回低风险结果"）
    use_fake_providers: bool = False

    # ---- 视觉理解（Qwen2.5-VL via 本地 Ollama，凭证一致性校验）----
    ollama_base_url: str = "http://localhost:11434"   # Ollama 服务地址
    vision_model: str = "qwen2.5vl:7b"               # 视觉语言模型
    vision_timeout_seconds: int = 120                # VL 推理超时（本地 7B 较慢）
    vision_enabled: bool = True                      # 总开关；关闭则跳过 VL（静默降级）
    evidence_inconsistent_penalty: float = 0.30      # 凭证与描述不一致 → 风险分 +30 分

    # ---- 工单5 移植：批量审批沙箱（SANDBOX_MODE off/on 双模式）----
    sandbox_mode: str = "off"            # off=宿主机直读（漏洞基线） on=真沙箱隔离解析
    e2b_api_url: str = "http://127.0.0.1:13000"
    e2b_api_key: str = ""
    cube_template_id: str = ""
    e2b_sandbox_url: str = ""            # 本地 CubeSandbox 执行端点改写（支持 {sandbox_id}）
    approval_batch_size: int = 50        # 批量审批单次上限
    approval_export_limit: int = 500     # 导出挂起单上限

    # ---- 数字人直播（算力云自部署：CosyVoice2 TTS + MuseTalk 口型）----
    # 渲染服务：AutoDL 实例上的 FastAPI（dh_api），本机经 SSH 隧道访问时填隧道地址。
    # 留空则直播接口返回未配置。渲染一句话术约 1~2 分钟，HTTP 超时要给足。
    dh_api_base_url: str = "http://127.0.0.1:8600"
    dh_api_timeout_seconds: int = 300
    dh_idle_video_path: str = "/videos/idle.mp4"   # 未播话术时前端循环的待机视频
    # 直播对话大脑（与工作流 LLM 分开配置：话术要口语自然，且模型可独立切换）
    live_llm_model: str = "deepseek-v4-flash"
    live_llm_temperature: float = 0.7
    live_llm_timeout_seconds: int = 20
    # 自动讲解（SOP 骨架 + LLM 轮换）
    live_touting_enabled: bool = True        # 开播后是否自动循环讲解
    live_touting_gap_seconds: float = 0.8    # 段落之间的换气停顿（0.5~3 之间较自然）
    live_touting_gap_jitter: float = 0.3     # 停顿抖动幅度（秒）：实际停在 gap±jitter 内随机，0=固定
    live_touting_use_llm: bool = True        # true=LLM 改写换说法；false=只用脚本骨架兜底话术

    # ---- 工单5 移植：Langfuse 链路追踪 ----
    langfuse_secret_key: str = ""
    langfuse_public_key: str = ""
    langfuse_base_url: str = "https://cloud.langfuse.com"
    langfuse_enabled: bool = True        # 关闭则跳过 Trace 上报（离线可降级）
    judge_model: str = "gpt-4o-mini"  # LLM-as-a-judge 裁判模型

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
