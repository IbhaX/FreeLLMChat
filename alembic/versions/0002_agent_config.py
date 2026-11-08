"""add agent config column

Revision ID: 0002_agent_config
Revises: 0001_initial
Create Date: 2026-11-07
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_agent_config"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_configs", sa.Column("config", sa.JSON(), nullable=True))
    op.execute("UPDATE agent_configs SET config = '{}' WHERE config IS NULL")


def downgrade() -> None:
    op.drop_column("agent_configs", "config")
