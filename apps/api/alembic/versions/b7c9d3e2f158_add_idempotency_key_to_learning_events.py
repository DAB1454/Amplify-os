"""add idempotency_key to learning_events

Revision ID: b7c9d3e2f158
Revises: a3f8b2d1e947
Create Date: 2026-03-20 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c9d3e2f158'
down_revision: Union[str, None] = 'a3f8b2d1e947'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'learning_events',
        sa.Column('idempotency_key', sa.String(length=255), nullable=True),
    )
    op.create_unique_constraint(
        'uq_learning_events_idempotency_key',
        'learning_events',
        ['idempotency_key'],
    )


def downgrade() -> None:
    op.drop_constraint('uq_learning_events_idempotency_key', 'learning_events', type_='unique')
    op.drop_column('learning_events', 'idempotency_key')
