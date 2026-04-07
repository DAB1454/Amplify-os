"""merge heads and add ON DELETE SET NULL to assisted_tasks.channel_id

Two heads existed (b2c3d4e5f678 + a1b2c3d4e501) — this migration merges them
and fixes the assisted_tasks.channel_id FK so that disconnecting a channel
doesn't fail with a constraint violation when assisted_tasks reference it.

Revision ID: c9d8e7f6a543
Revises: b2c3d4e5f678, a1b2c3d4e501
Create Date: 2026-04-07
"""
from alembic import op
import sqlalchemy as sa

revision = "c9d8e7f6a543"
down_revision = ("b2c3d4e5f678", "a1b2c3d4e501")
branch_labels = None
depends_on = None

# Inspect the live DB to find the actual FK name — naming conventions varied
# across earlier migrations, so do not hard-code.
def _find_fk_name(bind, table: str, referred_table: str) -> str | None:
    insp = sa.inspect(bind)
    for fk in insp.get_foreign_keys(table):
        if fk["referred_table"] == referred_table and "channel_id" in fk["constrained_columns"]:
            return fk["name"]
    return None


def upgrade() -> None:
    bind = op.get_bind()
    fk_name = _find_fk_name(bind, "assisted_tasks", "channel_connections")

    if fk_name:
        op.drop_constraint(fk_name, "assisted_tasks", type_="foreignkey")

    op.create_foreign_key(
        "fk_assisted_tasks_channel_id_channel_connections",
        "assisted_tasks",
        "channel_connections",
        ["channel_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_assisted_tasks_channel_id_channel_connections",
        "assisted_tasks",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_assisted_tasks_channel_id_channel_connections",
        "assisted_tasks",
        "channel_connections",
        ["channel_id"],
        ["id"],
    )
