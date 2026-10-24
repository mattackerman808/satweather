"""PIREP placement by /OV (satweather.pireps).

waypoint caches Tachbase's answers for identifiers (airports, navaids,
fixes): lat/lon NULL records a miss, remembered for a day. pirep gains
placed_at (when placement ran; NULL = not yet) and place_tries (failed
lookups, so an unreachable Tachbase is retried a few times, not forever).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-24
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE waypoint (
            id         text PRIMARY KEY,
            name       text,
            kind       text,            -- airport | navaid | fix (Tachbase's type)
            lat        double precision,
            lon        double precision,
            fetched_at timestamptz NOT NULL DEFAULT now()
        );
        ALTER TABLE pirep ADD COLUMN placed_at timestamptz,
                          ADD COLUMN place_tries smallint NOT NULL DEFAULT 0;
        CREATE INDEX pirep_unplaced ON pirep (id) WHERE placed_at IS NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS waypoint;
        ALTER TABLE pirep DROP COLUMN IF EXISTS placed_at, DROP COLUMN IF EXISTS place_tries;
        """
    )
