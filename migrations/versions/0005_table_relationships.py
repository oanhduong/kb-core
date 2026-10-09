"""table relationships (quan hệ giữa các bảng số liệu của workspace, dò bằng dữ liệu)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-09 15:00:00+00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "table_relationships",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("workspace_id", sa.String(length=32), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("left_table_id", sa.String(length=32), sa.ForeignKey("data_tables.id", ondelete="CASCADE"), nullable=False),
        sa.Column("left_column", sa.Text(), nullable=False),
        sa.Column("left_transforms", postgresql.JSONB(), nullable=False),
        sa.Column("right_table_id", sa.String(length=32), sa.ForeignKey("data_tables.id", ondelete="CASCADE"), nullable=False),
        sa.Column("right_column", sa.Text(), nullable=False),
        sa.Column("right_transforms", postgresql.JSONB(), nullable=False),
        sa.Column("cardinality", sa.String(length=16), nullable=False),
        sa.Column("matched_keys", sa.Integer(), nullable=False),
        sa.Column("left_coverage", sa.Float(), nullable=False),
        sa.Column("right_coverage", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reviewed_by", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("left_table_id", "left_column", "right_table_id", "right_column", name="uq_table_relationship"),
    )
    op.create_index("ix_table_relationships_ws", "table_relationships", ["workspace_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_table_relationships_ws", table_name="table_relationships")
    op.drop_table("table_relationships")
