"""add campaign modes

Revision ID: c4d5e6f7a890
Revises: b7c9d3e2f158
Create Date: 2026-03-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d5e6f7a890'
down_revision: Union[str, None] = 'b7c9d3e2f158'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'campaigns',
        sa.Column('mode', sa.String(length=20), server_default='manual', nullable=False),
    )
    op.add_column(
        'posts',
        sa.Column('approval_status', sa.String(length=30), nullable=True),
    )
    op.add_column(
        'posts',
        sa.Column('day_number', sa.Integer(), nullable=True),
    )
    op.add_column(
        'posts',
        sa.Column('action_type_label', sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('posts', 'action_type_label')
    op.drop_column('posts', 'day_number')
    op.drop_column('posts', 'approval_status')
    op.drop_column('campaigns', 'mode')
