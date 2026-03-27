"""add asset approval and generation columns

Revision ID: e6f7a8b9c012
Revises: d5e6f7a8b901
Create Date: 2026-03-27
"""
from alembic import op
import sqlalchemy as sa

revision = "e6f7a8b9c012"
down_revision = "d5e6f7a8b901"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assets", sa.Column("approval_status", sa.String(30), nullable=True))
    op.add_column("assets", sa.Column("generation_prompt", sa.Text(), nullable=True))
    op.add_column("assets", sa.Column("generation_cost", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("assets", "generation_cost")
    op.drop_column("assets", "generation_prompt")
    op.drop_column("assets", "approval_status")
