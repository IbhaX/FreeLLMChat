"""activity-log v2: normalized fields on agent_events

Revision ID: 0006_activity_events
Revises: 0005_research_v2
Create Date: 2026-02-21
"""
import sqlalchemy as sa

from alembic import op

revision = "0006_activity_events"
down_revision = "0005_research_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_events",
                  sa.Column("kind", sa.String(24), server_default="note",
                            nullable=False))
    op.add_column("agent_events",
                  sa.Column("title", sa.String(512), server_default="",
                            nullable=False))
    op.add_column("agent_events",
                  sa.Column("parent", sa.String(64), server_default="",
                            nullable=False))
    op.add_column("agent_events",
                  sa.Column("level", sa.String(16), server_default="normal",
                            nullable=False))
    op.add_column("agent_events", sa.Column("payload", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_events", "payload")
    op.drop_column("agent_events", "level")
    op.drop_column("agent_events", "parent")
    op.drop_column("agent_events", "title")
    op.drop_column("agent_events", "kind")
