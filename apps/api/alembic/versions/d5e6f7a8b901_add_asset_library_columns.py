"""Add asset library columns: artist_id, description, tags, source

Revision ID: d5e6f7a8b901
Revises: c4d5e6f7a890
Create Date: 2026-03-26

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision = "d5e6f7a8b901"
down_revision = "c4d5e6f7a890"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assets", sa.Column("artist_id", sa.Uuid(), nullable=True))
    op.add_column("assets", sa.Column("description", sa.Text(), server_default="", nullable=False))
    op.add_column("assets", sa.Column("tags", ARRAY(sa.String()), server_default="{}", nullable=False))
    op.add_column("assets", sa.Column("source", sa.String(30), server_default="uploaded", nullable=False))
    op.create_foreign_key(
        op.f("fk_assets_artist_id_artists"),
        "assets",
        "artists",
        ["artist_id"],
        ["id"],
    )
    op.create_index(op.f("ix_assets_artist_id"), "assets", ["artist_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_assets_artist_id"), table_name="assets")
    op.drop_constraint(op.f("fk_assets_artist_id_artists"), "assets", type_="foreignkey")
    op.drop_column("assets", "source")
    op.drop_column("assets", "tags")
    op.drop_column("assets", "description")
    op.drop_column("assets", "artist_id")
