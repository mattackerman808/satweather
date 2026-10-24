"""Tropical cyclones: products tied to their storm, and each advisory's numbers.

text_product.storm_id is the ATCF id (EP222026) from the header of an
advisory package product (TCM, TCP, TCD, PWS, TCA ...). tc_advisory holds
what satweather.tropical.parse_tcm reads from each forecast/advisory:
center, motion, pressure, winds, wind radii, the previous position and
the forecast points, as NHC wrote them. Corrections replace the row.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-24
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
        ALTER TABLE text_product ADD COLUMN storm_id text;
        UPDATE text_product SET storm_id = substring(left(raw, 800) from '\m((AL|EP|CP)\d{2}20\d{2})\M')
         WHERE nnn IN ('TCM', 'TCP', 'TCD', 'TCU', 'PWS', 'TCA', 'TCE', 'TCV', 'TCB', 'TCR');
        CREATE INDEX text_product_storm ON text_product (storm_id, received DESC) WHERE storm_id IS NOT NULL;

        CREATE TABLE tc_advisory (
            storm_id    text NOT NULL,              -- EP222026
            number      text NOT NULL,              -- '11'
            product_id  bigint NOT NULL REFERENCES text_product ON DELETE CASCADE,
            basin       text NOT NULL,              -- AL | EP | CP
            issued      timestamptz NOT NULL,
            kind        text NOT NULL,              -- Hurricane, Tropical Storm, ...
            name        text NOT NULL,
            lat         double precision NOT NULL,
            lon         double precision NOT NULL,
            valid       timestamptz NOT NULL,       -- of the center position
            move_dir    smallint,
            move_kt     smallint,
            pressure_mb smallint,
            wind_kt     smallint,
            gust_kt     smallint,
            radii       jsonb NOT NULL,             -- {"64": [NE, SE, SW, NW] nm, "50": ..., "34": ...}
            prev        jsonb,                      -- {"valid", "lat", "lon"}
            forecast    jsonb NOT NULL,             -- [{"valid", "lat", "lon", "outlook", "wind_kt", ...}]
            final       boolean NOT NULL DEFAULT false,
            PRIMARY KEY (storm_id, number)
        );
        CREATE INDEX tc_advisory_issued ON tc_advisory (issued DESC);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS tc_advisory;
        ALTER TABLE text_product DROP COLUMN IF EXISTS storm_id;
        """
    )
