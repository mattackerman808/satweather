"""station.priority: aviationweather.gov's global importance (0 = most).

Used with Tachbase's airport classes to thin stations by zoom on the map
(satweather.airport_classes). Filled by satweather-refdata.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-24
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE station ADD COLUMN priority smallint;
        CREATE INDEX station_priority ON station (priority);
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE station DROP COLUMN IF EXISTS priority")
