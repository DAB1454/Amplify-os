"""add automation_level + automation_paused to tenants

Foundation column for the autonomy graduation ladder. Default 'manual'
so existing tenants are unaffected; the worker tick skips them entirely.

Revision ID: f8b9c0d1e234
Revises: e2f3a4b5c678
Create Date: 2026-04-08
"""
from alembic import op
import sqlalchemy as sa

revision = "f8b9c0d1e234"
down_revision = "e2f3a4b5c678"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "automation_level",
            sa.String(length=20),
            nullable=False,
            server_default="manual",
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "automation_paused",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "automation_paused")
    op.drop_column("tenants", "automation_level")
