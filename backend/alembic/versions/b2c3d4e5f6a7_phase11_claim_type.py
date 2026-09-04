"""phase11 工单8 交叉校验：RefundCase 增加用户显式选择的售后类型列（claim_type）

Revision ID: b2c3d4e5f6a7
Revises: a9b8c7d6e5f4
Create Date: 2026-09-04 17:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a9b8c7d6e5f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 买家端显式三选一（退款/退货退款/换货）；员工侧/旧案件为 NULL（保持双层意图识别主用）
    op.add_column('refund_cases', sa.Column('claim_type', sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column('refund_cases', 'claim_type')
