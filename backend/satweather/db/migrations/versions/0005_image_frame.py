"""image_frame: one row per image (satellite, radar mosaic, chart) and its web copies.

satweather-ingest catalogs each settled image (pending): satellite PNGs
and LRIT charts under IMAGES/, and EMWIN graphics (radar mosaics, NWS
maps) as they arrive in EMWIN/;
satweather-imaging claims pending rows newest first (FOR UPDATE SKIP
LOCKED, so the row is the job and a crash just rolls the claim back),
writes the WebP display copy and thumbnail under media/, and marks the
row ok, or dup when its original is byte-identical to an earlier one of
the same product (LRIT charts are re-sent hourly). Originals stay on the
share, which keeps only a few hours; the copies are what loops are
built from.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-24
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE image_frame (
            id           bigserial PRIMARY KEY,
            path         text NOT NULL UNIQUE,     -- relative to data_root, where it first appeared
            source       text NOT NULL,            -- GOES-18 | GOES-19 | Himawari | NWS
            sector       text NOT NULL,            -- Full Disk | Mesoscale 1 | Mesoscale 2 | Radar | Charts
            product      text NOT NULL,            -- band ('13'), composite ('ABI_False_Color'), AWIPS id ('RADREFUS')
            kind         text NOT NULL,            -- band | rgb | graphic
            overlay      boolean NOT NULL DEFAULT false,  -- *_map.png: borders drawn in
            scan_time    timestamptz NOT NULL,     -- scan start; for NWS graphics, when received
            size         bigint NOT NULL,
            mtime        timestamptz NOT NULL,     -- EMWIN files move to archive/<UTC date of mtime>/
            projection   jsonb,                    -- product.cbor projection_cfg (geos)
            catalogued_at timestamptz NOT NULL DEFAULT now(),
            status       text NOT NULL DEFAULT 'pending',  -- pending | ok | dup | error | gone
            digest       text,                     -- sha256 of the original
            error        text,
            width        integer,                  -- of the display copy
            height       integer,
            display      text,                     -- relative to media_root
            thumb        text,
            made_at      timestamptz
        );
        CREATE INDEX image_frame_loop ON image_frame (source, sector, product, overlay, scan_time DESC)
            WHERE status = 'ok';
        CREATE INDEX image_frame_pending ON image_frame (scan_time DESC) WHERE status = 'pending';
        CREATE INDEX image_frame_digest ON image_frame (source, sector, product, digest) WHERE status = 'ok';
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS image_frame")
