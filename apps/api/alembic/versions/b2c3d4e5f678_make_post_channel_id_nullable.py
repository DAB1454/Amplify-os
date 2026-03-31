"""make post channel_id nullable

Revision ID: b2c3d4e5f678
Revises: a1b2c3d4e567
Create Date: 2026-03-31
"""
from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f678"
down_revision = "a1b2c3d4e567"
branch_labels = None
depends_on = None

FK_NAME = "fk_posts_channel_id_channel_connections"


def upgrade() -> None:
    # Make channel_id nullable so posts survive channel disconnect/reconnect
    op.alter_column(
        "posts",
        "channel_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    # Update FK to SET NULL on delete
    op.drop_constraint(FK_NAME, "posts", type_="foreignkey")
    op.create_foreign_key(
        FK_NAME,
        "posts",
        "channel_connections",
        ["channel_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(FK_NAME, "posts", type_="foreignkey")
    op.create_foreign_key(
        FK_NAME,
        "posts",
        "channel_connections",
        ["channel_id"],
        ["id"],
    )
    op.alter_column(
        "posts",
        "channel_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
