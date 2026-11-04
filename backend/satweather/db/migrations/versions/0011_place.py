"""US cities and towns for place search: the Census places gazetteer
(incorporated places and census-designated places, all states, DC and
Puerto Rico), satweather-refdata --only place.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-04
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE place (
            id     serial PRIMARY KEY,
            name   text NOT NULL,            -- "Davis", type suffix ("city", "CDP") removed
            state  text NOT NULL,            -- USPS code
            aland  bigint,                   -- land area, m^2: bigger places rank first
            geom   geometry(Point, 4326) NOT NULL
        );
        CREATE INDEX place_name_prefix ON place (lower(name) text_pattern_ops);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS place")
