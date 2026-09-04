"""pytest 公共 fixture：使用真实 PostgreSQL（开发容器）验证约束与并发。"""
import os

# 测试模式：禁止创建案件时发布 Stream 消息（避免与后台 Worker 竞争）
os.environ["TESTING"] = "1"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.database import Base, SessionLocal

TEST_ENGINE = None
TEST_SESSION = None


@pytest.fixture(scope="session")
def engine():
    """每个测试会话：建表（schema 与迁移一致；测试表在事务内回滚隔离）。"""
    global TEST_ENGINE
    if TEST_ENGINE is None:
        TEST_ENGINE = create_engine(get_settings().database_url)
        Base.metadata.create_all(TEST_ENGINE)
    return TEST_ENGINE


@pytest.fixture
def db(engine) -> Session:
    """每个测试：独立事务，结束后回滚，互不污染。"""
    conn = engine.connect()
    tx = conn.begin()
    session = Session(bind=conn)
    yield session
    session.close()
    tx.rollback()
    conn.close()


def cleanup_cases_by_marker(marker: str) -> None:
    """按标记清理测试案件及其子表（外键依赖顺序）。"""
    from app.domain.models import (
        AgentRun,
        AuditLog,
        CaseEvidence,
        RefundCase,
        RefundRecord,
        ReviewTask,
        RiskAssessment,
    )

    session = SessionLocal()
    try:
        case_ids = [c.id for c in session.query(RefundCase).filter(RefundCase.applicant_id == marker).all()]
        if not case_ids:
            return
        for model in (AuditLog, CaseEvidence, ReviewTask, AgentRun, RiskAssessment, RefundRecord):
            session.query(model).filter(model.case_id.in_(case_ids)).delete(synchronize_session=False)
        session.query(RefundCase).filter(RefundCase.id.in_(case_ids)).delete(synchronize_session=False)
        session.commit()
    finally:
        session.close()
