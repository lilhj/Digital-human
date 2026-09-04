"""phase9 工单8 意图识别落库

Revision ID: f1a2b3c4d5e6
Revises: ea542453fb26
Create Date: 2026-08-28 20:41:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = 'ea542453fb26'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 工单8 双层意图识别四列：供前端大屏/案件详情展示；旧案件为 NULL（nullable）
    op.add_column('refund_cases', sa.Column('intent', sa.String(20), nullable=True))
    op.add_column('refund_cases', sa.Column('intent_source', sa.String(20), nullable=True))
    op.add_column('refund_cases', sa.Column('intent_confidence', sa.Numeric(4, 3), nullable=True))
    op.add_column('refund_cases', sa.Column('intent_fallback', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('refund_cases', 'intent_fallback')
    op.drop_column('refund_cases', 'intent_confidence')
    op.drop_column('refund_cases', 'intent_source')
    op.drop_column('refund_cases', 'intent')
