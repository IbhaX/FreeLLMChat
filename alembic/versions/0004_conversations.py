"""add conversations table

Revision ID: 0004_conversations
Revises: 0003_memories
Create Date: 2026-11-07
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_conversations"
down_revision = "0003_memories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("client_id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column("messages", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("client_id"),
    )
    op.create_index("ix_conversations_client_id", "conversations", ["client_id"])


def downgrade() -> None:
    op.drop_index("ix_conversations_client_id", table_name="conversations")
    op.drop_table("conversations")
