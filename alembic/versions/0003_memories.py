"""add memories table

Revision ID: 0003_memories
Revises: 0002_agent_config
Create Date: 2026-11-07
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_memories"
down_revision = "0002_agent_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False, server_default="user"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("memories")
