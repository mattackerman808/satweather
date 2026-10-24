"""Decoded METARs, TAFs and PIREPs (Tachbase's decoder, satweather.wxdecode).

metar gains the decoded form and flight category. TAFs and PIREPs get
their own tables. PIREPs are placed at the station that filed them
(the first token, an FAA id): the OV location is usually a radial from a
navaid, and navaid positions are not in the reference data yet, so
`approx` marks the placement as "near", not "at".

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-24
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE metar ADD COLUMN decoded jsonb, ADD COLUMN flight_category text;
        CREATE INDEX metar_station_valid ON metar (station, valid DESC);

        ALTER TABLE station ADD COLUMN faa text;
        CREATE INDEX station_faa ON station (faa);

        CREATE TABLE taf (
            id          bigserial PRIMARY KEY,
            product_id  bigint NOT NULL REFERENCES text_product ON DELETE CASCADE,
            station     text NOT NULL,
            issued      timestamptz,
            valid_from  timestamptz,
            valid_to    timestamptz,
            raw         text NOT NULL,
            decoded     jsonb NOT NULL,
            UNIQUE (station, issued, raw)
        );
        CREATE INDEX taf_station_issued ON taf (station, issued DESC);

        CREATE TABLE pirep (
            id          bigserial PRIMARY KEY,
            product_id  bigint NOT NULL REFERENCES text_product ON DELETE CASCADE,
            station     text,
            observed    timestamptz,
            urgent      boolean NOT NULL DEFAULT false,
            hazards     text[] NOT NULL DEFAULT '{}',
            raw         text NOT NULL UNIQUE,
            decoded     jsonb NOT NULL,
            geom        geometry(Point, 4326),
            approx      boolean NOT NULL DEFAULT true
        );
        CREATE INDEX pirep_observed ON pirep (observed DESC);
        CREATE INDEX pirep_geom ON pirep USING gist (geom);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS pirep, taf;
        ALTER TABLE station DROP COLUMN IF EXISTS faa;
        DROP INDEX IF EXISTS metar_station_valid;
        ALTER TABLE metar DROP COLUMN IF EXISTS decoded, DROP COLUMN IF EXISTS flight_category;
        """
    )
