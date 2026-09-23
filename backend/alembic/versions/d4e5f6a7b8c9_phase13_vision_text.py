"""phase13 工单6 扩展：CaseEvidence 增加 Qwen2.5-VL 图片语义理解列（vision_text）

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-10 09:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 视觉理解描述（DLP 脱敏后落库；Ollama 不可用 / 未识别为 NULL，静默降级）
    op.add_column('case_evidences', sa.Column('vision_text', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('case_evidences', 'vision_text')
