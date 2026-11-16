"""roleplay: per-room web-tools toggle."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_roleplay_tools"
down_revision = "0007_roleplay_stage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "roleplay_rooms",
        sa.Column("tools_enabled", sa.Boolean(),
                  server_default="0", nullable=False))


def downgrade() -> None:
    op.drop_column("roleplay_rooms", "tools_enabled")
