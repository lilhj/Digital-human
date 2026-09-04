"""phase10 工单5 telemetry：AgentRun 记录 LLM token 用量

Revision ID: a9b8c7d6e5f4
Revises: f1a2b3c4d5e6
Create Date: 2026-09-03 21:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a9b8c7d6e5f4'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 节点内 LLM 调用的累计 token（真实 usage，非 Fake/规则路径的节点为 NULL）
    op.add_column('agent_runs', sa.Column('prompt_tokens', sa.Integer(), nullable=True))
    op.add_column('agent_runs', sa.Column('completion_tokens', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('agent_runs', 'completion_tokens')
    op.drop_column('agent_runs', 'prompt_tokens')