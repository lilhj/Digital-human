"""（已弃用）腾讯云智能数智人 IVH 云渲染交互 API 客户端。

数字人已切换为算力云自部署方案（CosyVoice2 + MuseTalk，见 renderer.py），
本模块仅保留两个异常类占位，防止历史 import 直接崩溃，其余内容已清空。
确认无引用后可手动删除本文件，并同时清理 .env 里的 IVH_* 配置。
"""


class IVHError(Exception):
    """（弃用）IVH 调用异常。"""


class IVHNotConfigured(IVHError):
    """（弃用）IVH 凭据未配置。"""
