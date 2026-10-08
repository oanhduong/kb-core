"""content translations (bản dịch nội dung AI sinh theo ngôn ngữ giao diện)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-08 18:00:00+00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "content_translations",
        sa.Column("workspace_id", sa.String(length=32), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lang", sa.String(length=8), nullable=False),
        sa.Column("source_hash", sa.String(length=40), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "lang", "source_hash"),
    )


def downgrade() -> None:
    op.drop_table("content_translations")
