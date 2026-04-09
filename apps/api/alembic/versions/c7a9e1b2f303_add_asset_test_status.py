"""add asset test_status + usage tracking columns

Supports Task #20: the planner needs to distinguish between proven
assets (used enough times to trust their engagement) and untested ones
(new uploads / AI generations that haven't been tried yet). A small
denormalized usage counter avoids a full join table for the common
"rank the library" query.

Revision ID: c7a9e1b2f303
Revises: c7a9e1b2f302
Create Date: 2026-04-09
"""
from alembic import op
import sqlalchemy as sa


revision = "c7a9e1b2f303"
down_revision = "c7a9e1b2f302"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column(
            "test_status",
            sa.String(length=20),
            nullable=False,
            server_default="untested",
        ),
    )
    op.add_column(
        "assets",
        sa.Column(
            "uses_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "assets",
        sa.Column("avg_engagement_score", sa.Float(), nullable=True),
    )
    op.add_column(
        "assets",
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_assets_tenant_test_status",
        "assets",
        ["tenant_id", "test_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_assets_tenant_test_status", table_name="assets")
    op.drop_column("assets", "last_used_at")
    op.drop_column("assets", "avg_engagement_score")
    op.drop_column("assets", "uses_count")
    op.drop_column("assets", "test_status")
