"""容器首次启动初始化：建业务表（alembic）+ 种子账号（seed）。

由 backend/worker 容器 CMD 前的 entrypoint 调用；幂等，可重复执行。
仅在数据库尚未初始化时执行迁移，避免多实例同时 `alembic upgrade` 竞争。
"""
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("init_db")
# 让 alembic 能定位到 alembic.ini（与 scripts/ 同级）
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def db_initialized() -> bool:
    """判断业务表是否已经建好（以 users 表为锚点）。"""
    from sqlalchemy import create_engine, inspect

    url = os.environ.get("DATABASE_URL", "")
    engine = create_engine(url)
    try:
        return inspect(engine).has_table("users")
    finally:
        engine.dispose()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    try:
        if db_initialized():
            logger.info("数据库已初始化，跳过迁移与种子")
            return 0
        logger.info("全新数据库，执行 alembic 迁移 + 种子")
        here = str(BACKEND_ROOT)
        env = dict(os.environ)
        for cmd in (["python", "-m", "alembic", "upgrade", "head"],
                    ["python", "-m", "scripts.seed"]):
            logger.info("运行: %s", " ".join(cmd))
            subprocess.run(cmd, cwd=here, env=env, check=True)
        logger.info("数据库初始化完成（业务表 + 4 个种子账号）")
        return 0
    except Exception as e:  # noqa: BLE001
        logger.error("数据库初始化失败: %s", e, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())