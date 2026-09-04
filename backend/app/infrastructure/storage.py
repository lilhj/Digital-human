"""本地凭证存储（L-5 修复）：uploads 目录解析 + image_url 路径归一化。

一处定义、三处共用，避免各模块各自推导目录导致路径漂移：
- cases.py 写入：返回可被静态服务访问的 URL 路径 `/uploads/{name}`；
- main.py 静态挂载：`app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR))`；
- nodes.py OCR Worker：把 URL 样式路径还原为磁盘绝对路径再喂给 OCR。

兼容旧数据：历史记录 `uploads/xxx.jpg`（相对）与新格式 `/uploads/xxx.jpg`
（URL 路径带前导斜杠）统一取文件名映射到 UPLOAD_DIR，rSplit 天然规避两种格式差异。
"""
import os
from pathlib import Path

# backend/app/infrastructure/storage.py -> L3/L2/L1 = platform/backend/app/infrastructure
UPLOAD_DIR = Path(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))) / "uploads"


def get_upload_dir() -> Path:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return UPLOAD_DIR


def image_url_to_disk(image_url: str) -> str:
    """把落库 image_url（`/uploads/x.jpg` 或旧 `uploads/x.jpg`）解析为磁盘绝对路径。

    文件名取自 URL 最后一段（文件名由服务端 uuid 生成，无目录穿越面），
    与 UPLOAD_DIR 拼接后交给 OCR/后续处理；文件不存在时原样返回（Fake 忽略，
    Paddle 用真实路径）。
    """
    if not image_url:
        return ""
    name = image_url.rstrip("/").rsplit("/", 1)[-1]
    return str(get_upload_dir() / name)