"""roleplay stage: room summary/mood/paused/director notes columns."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_roleplay_stage"
down_revision = "0006_activity_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("roleplay_rooms",
                  sa.Column("summary", sa.Text(), server_default="",
                            nullable=False))
    op.add_column("roleplay_rooms",
                  sa.Column("mood", sa.String(32), server_default="",
                            nullable=False))
    op.add_column("roleplay_rooms",
                  sa.Column("paused_ids", sa.JSON(), nullable=True))
    op.add_column("roleplay_rooms",
                  sa.Column("director_notes", sa.Text(), server_default="",
                            nullable=False))


def downgrade() -> None:
    op.drop_column("roleplay_rooms", "director_notes")
    op.drop_column("roleplay_rooms", "paused_ids")
    op.drop_column("roleplay_rooms", "mood")
    op.drop_column("roleplay_rooms", "summary")
