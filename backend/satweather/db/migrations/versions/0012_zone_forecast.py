"""The newest forecast segment for each zone, so the zone panel, the
Forecast tab and the place search's ZFP city lists don't search
product_segment.

Finding "the newest ZFP naming CAZ508" in product_segment means walking
every segment that ever named the zone (or every ZFP ever stored, when
none names it): seconds to minutes on the data disk. zone_forecast keeps
the answer, one row per (zone, product type), for the types that are a
zone's forecast (api/zones.py FORECAST_NNN: ZFP, CWF, NSH, OFF).

A trigger on product_segment fills it rather than store.py, so it holds
for every way segments are written (ingest, tests) and for segments
the previous code ingests between this migration and the restart.
Segments are never updated after insert. The backfill takes the newest
week; a zone with no forecast in that time has none until its next one.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-13
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE zone_forecast (
            ugc        text NOT NULL,
            nnn        text NOT NULL,
            segment_id bigint NOT NULL REFERENCES product_segment ON DELETE CASCADE,
            product_id bigint NOT NULL REFERENCES text_product ON DELETE CASCADE,
            issued     timestamptz NOT NULL,     -- coalesce(issued, received) of the product
            PRIMARY KEY (ugc, nnn)
        );
        CREATE INDEX zone_forecast_segment ON zone_forecast (segment_id);

        CREATE FUNCTION zone_forecast_track() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            p record;
        BEGIN
            SELECT nnn, coalesce(issued, received) AS at INTO p FROM text_product WHERE id = NEW.product_id;
            IF p.nnn IN ('ZFP', 'CWF', 'NSH', 'OFF') THEN
                INSERT INTO zone_forecast (ugc, nnn, segment_id, product_id, issued)
                SELECT DISTINCT u, p.nnn, NEW.id, NEW.product_id, p.at FROM unnest(NEW.ugcs) u
                ON CONFLICT (ugc, nnn) DO UPDATE
                    SET segment_id = EXCLUDED.segment_id, product_id = EXCLUDED.product_id, issued = EXCLUDED.issued
                    WHERE EXCLUDED.issued >= zone_forecast.issued;
            END IF;
            RETURN NULL;
        END $$;
        CREATE TRIGGER product_segment_zone_forecast AFTER INSERT ON product_segment
            FOR EACH ROW EXECUTE FUNCTION zone_forecast_track();

        INSERT INTO zone_forecast (ugc, nnn, segment_id, product_id, issued)
        SELECT DISTINCT ON (u, p.nnn) u, p.nnn, s.id, p.id, coalesce(p.issued, p.received)
        FROM text_product p JOIN product_segment s ON s.product_id = p.id, unnest(s.ugcs) u
        WHERE p.nnn IN ('ZFP', 'CWF', 'NSH', 'OFF') AND p.received > now() - interval '7 days'
        ORDER BY u, p.nnn, coalesce(p.issued, p.received) DESC, s.id DESC;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS product_segment_zone_forecast ON product_segment;
        DROP FUNCTION IF EXISTS zone_forecast_track();
        DROP TABLE IF EXISTS zone_forecast;
        """
    )
