"""直播模块：算力云自部署数字人（CosyVoice2 TTS + MuseTalk 口型）接入。

- renderer：算力云渲染服务客户端（文本 → /say → mp4 地址）
- dialogue ：弹幕 → 带货话术（agicto · deepseek 文本模型，带兜底）
- service  ：单路直播会话管理器（进程内单例）
"""
from app.live.service import LiveSessionManager, manager, opening_line_for

__all__ = ["LiveSessionManager", "manager", "opening_line_for"]

