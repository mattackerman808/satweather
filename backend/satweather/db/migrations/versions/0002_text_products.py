"""EMWIN ingest: source files, text products, segments, VTEC state, METARs,
and the UGC zone / station reference tables.

VTEC events are tracked at two levels because that is how the NWS issues
them: an event (office.phenomena.significance.etn.year) has one current
polygon and expiry, but its counties or zones are continued, extended and
cancelled one segment at a time. A partial cancellation leaves the event
active for the UGCs that were not cancelled, so "active" is decided per UGC.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-24
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE source_file (
            id          bigserial PRIMARY KEY,
            name        text NOT NULL UNIQUE,      -- filename; location derives from it + mtime
            kind        text NOT NULL,             -- emwin_text | emwin_image | unknown
            size        integer NOT NULL,
            mtime       timestamptz NOT NULL,
            received    timestamptz,               -- from the filename (KWIN receive time)
            awips       text,
            wmo         text,
            cccc        text,
            status      text NOT NULL,             -- ok | raw | error | skipped
            error       text,
            ingested_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX source_file_kind_received ON source_file (kind, received DESC);
        CREATE INDEX source_file_awips_received ON source_file (awips, received DESC);

        CREATE TABLE text_product (
            id            bigserial PRIMARY KEY,
            source_file_id bigint NOT NULL UNIQUE REFERENCES source_file ON DELETE CASCADE,
            wmo           text NOT NULL,
            cccc          text NOT NULL,
            awips         text NOT NULL,
            nnn           text NOT NULL,
            office        text NOT NULL,
            issued        timestamptz,
            received      timestamptz NOT NULL,
            priority      smallint NOT NULL,
            title         text NOT NULL,
            category      text NOT NULL,
            raw           text NOT NULL,
            parse_error   text,
            tsv tsvector GENERATED ALWAYS AS (
                setweight(to_tsvector('english', title), 'A') || to_tsvector('english', raw)
            ) STORED
        );
        CREATE INDEX text_product_received ON text_product (received DESC, id DESC);
        CREATE INDEX text_product_nnn ON text_product (nnn, received DESC);
        CREATE INDEX text_product_category ON text_product (category, received DESC);
        CREATE INDEX text_product_office ON text_product (office, received DESC);
        CREATE INDEX text_product_awips ON text_product (awips, received DESC);
        CREATE INDEX text_product_tsv ON text_product USING gin (tsv);

        CREATE TABLE product_segment (
            id          bigserial PRIMARY KEY,
            product_id  bigint NOT NULL REFERENCES text_product ON DELETE CASCADE,
            seq         smallint NOT NULL,
            ugcs        text[] NOT NULL,
            ugc_expire  timestamptz,
            headlines   text[] NOT NULL,
            body        text NOT NULL,
            polygon     geometry(Polygon, 4326),
            motion      jsonb,
            tags        jsonb NOT NULL DEFAULT '{}'
        );
        CREATE INDEX product_segment_product ON product_segment (product_id);
        CREATE INDEX product_segment_ugcs ON product_segment USING gin (ugcs);
        CREATE INDEX product_segment_polygon ON product_segment USING gist (polygon);

        CREATE TABLE vtec (
            id           bigserial PRIMARY KEY,
            product_id   bigint NOT NULL REFERENCES text_product ON DELETE CASCADE,
            segment_id   bigint NOT NULL REFERENCES product_segment ON DELETE CASCADE,
            action       text NOT NULL,
            office       text NOT NULL,
            phenomena    text NOT NULL,
            significance text NOT NULL,
            etn          integer NOT NULL,
            year         smallint NOT NULL,
            begins       timestamptz,
            ends         timestamptz
        );
        CREATE INDEX vtec_event ON vtec (office, phenomena, significance, etn, year);
        CREATE INDEX vtec_product ON vtec (product_id);

        CREATE TABLE vtec_event_state (
            office       text NOT NULL,
            phenomena    text NOT NULL,
            significance text NOT NULL,
            etn          integer NOT NULL,
            year         smallint NOT NULL,
            action       text NOT NULL,
            begins       timestamptz,
            ends         timestamptz,
            polygon      geometry(Polygon, 4326),
            product_id   bigint NOT NULL REFERENCES text_product ON DELETE CASCADE,
            issued       timestamptz NOT NULL,
            PRIMARY KEY (office, phenomena, significance, etn, year)
        );
        CREATE INDEX vtec_event_state_ends ON vtec_event_state (ends);

        CREATE TABLE vtec_ugc_state (
            office       text NOT NULL,
            phenomena    text NOT NULL,
            significance text NOT NULL,
            etn          integer NOT NULL,
            year         smallint NOT NULL,
            ugc          text NOT NULL,
            action       text NOT NULL,
            ends         timestamptz,
            product_id   bigint NOT NULL REFERENCES text_product ON DELETE CASCADE,
            issued       timestamptz NOT NULL,
            PRIMARY KEY (office, phenomena, significance, etn, year, ugc)
        );
        CREATE INDEX vtec_ugc_state_ends ON vtec_ugc_state (ends);

        CREATE TABLE metar (
            id           bigserial PRIMARY KEY,
            product_id   bigint NOT NULL REFERENCES text_product ON DELETE CASCADE,
            station      text NOT NULL,
            valid        timestamptz NOT NULL,
            kind         text NOT NULL,
            raw          text NOT NULL,
            temp_c       real,
            dewpoint_c   real,
            wind_dir     real,
            wind_kt      real,
            gust_kt      real,
            vis_mi       real,
            altimeter_in real,
            UNIQUE (station, valid, kind)
        );
        CREATE INDEX metar_valid ON metar (valid DESC);

        -- Reference data (satweather-refdata, monthly).
        CREATE TABLE ugc_zone (
            kind     text NOT NULL,   -- public | county | fire | marine | offshore
            ugc      text NOT NULL,
            name     text NOT NULL,
            state    text,
            cwa      text,
            geom     geometry(MultiPolygon, 4326) NOT NULL,
            centroid geometry(Point, 4326) NOT NULL,
            source   text NOT NULL,   -- shapefile it came from, e.g. z_16ap26
            PRIMARY KEY (kind, ugc)
        );
        CREATE INDEX ugc_zone_ugc ON ugc_zone (ugc);
        CREATE INDEX ugc_zone_geom ON ugc_zone USING gist (geom);

        CREATE TABLE station (
            id       text PRIMARY KEY,
            icao     text,
            name     text,
            state    text,
            country  text,
            elev_m   real,
            geom     geometry(Point, 4326) NOT NULL
        );
        CREATE INDEX station_geom ON station USING gist (geom);

        CREATE TABLE refdata_load (
            kind     text PRIMARY KEY,
            source   text NOT NULL,
            rows     integer NOT NULL,
            loaded   timestamptz NOT NULL DEFAULT now()
        );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS refdata_load, station, ugc_zone, metar,
            vtec_ugc_state, vtec_event_state, vtec, product_segment,
            text_product, source_file CASCADE;
        """
    )
