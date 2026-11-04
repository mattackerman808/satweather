"""Forecast tab: Point Forecast Matrix points, and cached LLM output.

pfm_point holds every point of every PFM issuance (pfm.parse_pfm), so the
Forecast tab can find the nearest point to a place and compare the newest
issuance with the one before it. Housekeeping keeps three days.

llm_output caches what the local LLM wrote (llm.py): one row per
(kind, key), regenerated only when inputs_hash (the source products it
was written from) changes.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-04
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE pfm_point (
            id            bigserial PRIMARY KEY,
            product_id    bigint NOT NULL REFERENCES text_product(id) ON DELETE CASCADE,
            office        text NOT NULL,
            ugc           text NOT NULL,
            name          text NOT NULL,
            geom          geometry(Point, 4326) NOT NULL,
            elev_ft       integer,
            issued        timestamptz NOT NULL,
            expires       timestamptz,
            utc_offset_h  smallint NOT NULL,          -- local = UTC + this (from the hour headers)
            series        jsonb NOT NULL,             -- 3-hourly then 6-hourly values
            twelve        jsonb NOT NULL,             -- 12-hour max/min, PoP, QPF, snow
            extra         jsonb
        );
        CREATE INDEX pfm_point_geom ON pfm_point USING gist (geom);
        CREATE INDEX pfm_point_name_issued ON pfm_point (office, name, issued DESC);
        CREATE INDEX pfm_point_product ON pfm_point (product_id);

        CREATE TABLE llm_output (
            kind          text NOT NULL,              -- outlook | afd
            key           text NOT NULL,
            inputs_hash   text NOT NULL,
            model         text,
            created       timestamptz NOT NULL DEFAULT now(),
            content       jsonb,
            error         text,
            PRIMARY KEY (kind, key)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS llm_output; DROP TABLE IF EXISTS pfm_point;")
