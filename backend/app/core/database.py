"""数据库基础设施：engine / SessionLocal / Base（业务模型 Phase 2 定义）。"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import get_settings

settings = get_settings()

# 性能优化（Phase 11）：默认 pool_size=5 在高并发下排队明显，调大连接池
# 配合 uvicorn --workers 4（每个进程独立连接池），避免 PG 连接成为瓶颈
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=20,
    pool_timeout=10,
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI 依赖：请求级数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
