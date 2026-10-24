"""image_frame: one row per satellite image on the share, and its web copies.

satweather-ingest catalogs each settled PNG under IMAGES/ (pending);
satweather-imaging claims pending rows newest first (FOR UPDATE SKIP
LOCKED, so the row is the job and a crash just rolls the claim back),
writes the WebP display copy and thumbnail under media/, and marks the
row ok. Originals stay on the share, which keeps only a few hours; the
copies are what loops are built from.

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
            path         text NOT NULL UNIQUE,     -- relative to data_root, as on the share
            satellite    text NOT NULL,            -- GOES-18 | GOES-19 | Himawari
            sector       text NOT NULL,            -- Full Disk | Mesoscale 1 | Mesoscale 2
            product      text NOT NULL,            -- band number ('13') or composite ('ABI_False_Color')
            kind         text NOT NULL,            -- band | rgb
            overlay      boolean NOT NULL DEFAULT false,  -- *_map.png: borders drawn in
            scan_time    timestamptz NOT NULL,
            size         bigint NOT NULL,
            projection   jsonb,                    -- product.cbor projection_cfg (geos)
            catalogued_at timestamptz NOT NULL DEFAULT now(),
            status       text NOT NULL DEFAULT 'pending',  -- pending | ok | error | gone
            error        text,
            width        integer,                  -- of the display copy
            height       integer,
            display      text,                     -- relative to media_root
            thumb        text,
            made_at      timestamptz
        );
        CREATE INDEX image_frame_loop ON image_frame (satellite, sector, product, overlay, scan_time DESC)
            WHERE status = 'ok';
        CREATE INDEX image_frame_pending ON image_frame (scan_time DESC) WHERE status = 'pending';
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS image_frame")
