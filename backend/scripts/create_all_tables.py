"""一次性建表引导脚本：仅创建缺失表，不动已有 alembic 管理的表。

用法：
    cd backend
    python scripts/create_all_tables.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine

from app.core.config import get_settings
from app.core.database import Base

# 必须导入模型模块，让所有表注册到 Base.metadata（database.py 只定义 Base，不导入模型）
import app.domain.models  # noqa: F401


def main() -> None:
    engine = create_engine(get_settings().database_url)
    # create_all 幂等：只建库里还没有的表，已存在的跳过
    Base.metadata.create_all(engine)
    print("tables ensured (missing ones created)")


if __name__ == "__main__":
    main()
