"""add automation_audits table

The agent decision trail. Every worker tick writes one row so the user
can see exactly what the autonomous agent did (or didn't do) while they
were away. Distinct from audit_logs (human-initiated changes).

Revision ID: a1b2c3d4e567
Revises: f8b9c0d1e234
Create Date: 2026-04-08
"""
from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e567"
down_revision = "f8b9c0d1e234"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "automation_audits",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "outcome",
            sa.String(length=20),
            nullable=False,
            server_default="success",
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("entity_type", sa.String(length=40), nullable=True),
        sa.Column("entity_id", sa.UUID(), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_automation_audits_tenant_id",
        "automation_audits",
        ["tenant_id"],
    )
    op.create_index(
        "ix_automation_audits_tenant_created",
        "automation_audits",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_automation_audits_tenant_action",
        "automation_audits",
        ["tenant_id", "action"],
    )


def downgrade() -> None:
    op.drop_index("ix_automation_audits_tenant_action", table_name="automation_audits")
    op.drop_index("ix_automation_audits_tenant_created", table_name="automation_audits")
    op.drop_index("ix_automation_audits_tenant_id", table_name="automation_audits")
    op.drop_table("automation_audits")
