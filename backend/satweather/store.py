"""Writing ingested files to the database.

One transaction per file. pg_notify fires inside it, so listeners (the
API's SSE stream) hear about a product only once it is committed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import Connection, text

from satweather.emwin import EmwinName
from satweather.parse import Parsed
from satweather.reports import metar as decode_metar

CHANNEL = "satweather"

# Actions that end an event for the UGCs they name.
ENDING = ("CAN", "EXP", "UPG")


def _insert_source(conn: Connection, *, name: str, kind: str, size: int, mtime: float, en: EmwinName | None,
                   status: str, error: str | None = None) -> int:
    return conn.execute(
        text(
            """
            INSERT INTO source_file (name, kind, size, mtime, received, awips, wmo, cccc, status, error)
            VALUES (:name, :kind, :size, :mtime, :received, :awips, :wmo, :cccc, :status, :error)
            ON CONFLICT (name) DO UPDATE SET status = EXCLUDED.status, error = EXCLUDED.error,
                size = EXCLUDED.size, mtime = EXCLUDED.mtime, ingested_at = now()
            RETURNING id
            """
        ),
        {
            "name": name,
            "kind": kind,
            "size": size,
            "mtime": datetime.fromtimestamp(mtime, UTC),
            "received": en.received if en else None,
            "awips": en.awips_long if en else None,
            "wmo": en.wmo if en else None,
            "cccc": en.cccc if en else None,
            "status": status,
            "error": error,
        },
    ).scalar_one()


def notify(conn: Connection, payload: dict) -> None:
    conn.execute(text("SELECT pg_notify(:ch, :p)"), {"ch": CHANNEL, "p": json.dumps(payload, default=str)})


def store_other(conn: Connection, *, name: str, kind: str, size: int, mtime: float, en: EmwinName | None,
                status: str, error: str | None = None) -> int:
    sid = _insert_source(conn, name=name, kind=kind, size=size, mtime=mtime, en=en, status=status, error=error)
    if kind == "emwin_image" and en:
        notify(conn, {"type": "image", "source_id": sid, "awips": en.awips_long, "received": en.received})
    return sid


def store_text(conn: Connection, *, name: str, size: int, mtime: float, en: EmwinName, raw: str,
               parsed: Parsed) -> int:
    status = "raw" if parsed.error else "ok"
    sid = _insert_source(conn, name=name, kind="emwin_text", size=size, mtime=mtime, en=en, status=status,
                         error=parsed.error)
    issued = parsed.issued
    pid = conn.execute(
        text(
            """
            INSERT INTO text_product (source_file_id, wmo, cccc, awips, nnn, office, issued, received,
                                      priority, title, category, raw, parse_error)
            VALUES (:sid, :wmo, :cccc, :awips, :nnn, :office, :issued, :received, :priority, :title,
                    :category, :raw, :err)
            ON CONFLICT (source_file_id) DO NOTHING
            RETURNING id
            """
        ),
        {
            "sid": sid, "wmo": en.wmo, "cccc": en.cccc, "awips": parsed.afos, "nnn": parsed.nnn,
            "office": parsed.office, "issued": issued, "received": en.received, "priority": en.priority,
            "title": parsed.title, "category": parsed.category, "raw": raw, "err": parsed.error,
        },
    ).scalar_one_or_none()
    if pid is None:  # already stored (re-ingest of the same file)
        return sid

    when = issued or en.received
    for seg in parsed.segments:
        seg_id = conn.execute(
            text(
                """
                INSERT INTO product_segment (product_id, seq, ugcs, ugc_expire, headlines, body, polygon,
                                             motion, tags)
                VALUES (:pid, :seq, :ugcs, :exp, :hl, :body,
                        CASE WHEN CAST(:poly AS text) IS NULL THEN NULL
                             ELSE ST_SetSRID(ST_GeomFromText(:poly), 4326) END,
                        CAST(:motion AS jsonb), CAST(:tags AS jsonb))
                RETURNING id
                """
            ),
            {
                "pid": pid, "seq": seg.seq, "ugcs": seg.ugcs, "exp": seg.ugc_expire, "hl": seg.headlines,
                "body": seg.text, "poly": seg.polygon_wkt,
                "motion": json.dumps(seg.motion) if seg.motion else None, "tags": json.dumps(seg.tags),
            },
        ).scalar_one()
        for v in seg.vtec:
            ends = v.ends or seg.ugc_expire
            key = {"office": v.office, "ph": v.phenomena, "sig": v.significance, "etn": v.etn, "year": v.year}
            conn.execute(
                text(
                    """
                    INSERT INTO vtec (product_id, segment_id, action, office, phenomena, significance, etn,
                                      year, begins, ends)
                    VALUES (:pid, :seg, :action, :office, :ph, :sig, :etn, :year, :begins, :ends)
                    """
                ),
                {**key, "pid": pid, "seg": seg_id, "action": v.action, "begins": v.begins, "ends": ends},
            )
            # Event: newest product wins; a product without a polygon keeps
            # the last one (zone-based events never have one).
            conn.execute(
                text(
                    """
                    INSERT INTO vtec_event_state (office, phenomena, significance, etn, year, action, begins,
                                                  ends, polygon, product_id, issued)
                    VALUES (:office, :ph, :sig, :etn, :year, :action, :begins, :ends,
                            CASE WHEN CAST(:poly AS text) IS NULL THEN NULL
                                 ELSE ST_SetSRID(ST_GeomFromText(:poly), 4326) END,
                            :pid, :when)
                    ON CONFLICT (office, phenomena, significance, etn, year) DO UPDATE SET
                        action = EXCLUDED.action,
                        begins = COALESCE(vtec_event_state.begins, EXCLUDED.begins),
                        ends = EXCLUDED.ends,
                        polygon = COALESCE(EXCLUDED.polygon, vtec_event_state.polygon),
                        product_id = EXCLUDED.product_id,
                        issued = EXCLUDED.issued
                    WHERE EXCLUDED.issued >= vtec_event_state.issued
                    """
                ),
                {**key, "action": v.action, "begins": v.begins, "ends": ends, "poly": seg.polygon_wkt,
                 "pid": pid, "when": when},
            )
            for ugc in seg.ugcs:
                conn.execute(
                    text(
                        """
                        INSERT INTO vtec_ugc_state (office, phenomena, significance, etn, year, ugc, action,
                                                    ends, product_id, issued)
                        VALUES (:office, :ph, :sig, :etn, :year, :ugc, :action, :ends, :pid, :when)
                        ON CONFLICT (office, phenomena, significance, etn, year, ugc) DO UPDATE SET
                            action = EXCLUDED.action, ends = EXCLUDED.ends,
                            product_id = EXCLUDED.product_id, issued = EXCLUDED.issued
                        WHERE EXCLUDED.issued >= vtec_ugc_state.issued
                        """
                    ),
                    {**key, "ugc": ugc, "action": v.action, "ends": ends, "pid": pid, "when": when},
                )

    for m in parsed.metars:
        try:
            decoded, cat = decode_metar(m.raw)
        except Exception:
            decoded, cat = None, None
        conn.execute(
            text(
                """
                INSERT INTO metar (product_id, station, valid, kind, raw, temp_c, dewpoint_c, wind_dir,
                                   wind_kt, gust_kt, vis_mi, altimeter_in, decoded, flight_category)
                VALUES (:pid, :station, :valid, :kind, :raw, :t, :td, :wd, :ws, :wg, :vis, :alt,
                        CAST(:dec AS jsonb), :cat)
                ON CONFLICT (station, valid, kind) DO NOTHING
                """
            ),
            {
                "pid": pid, "station": m.station, "valid": m.valid.replace(tzinfo=UTC) if m.valid.tzinfo is None
                else m.valid, "kind": m.kind, "raw": m.raw, "t": m.temp_c, "td": m.dewpoint_c,
                "wd": m.wind_dir, "ws": m.wind_kt, "wg": m.gust_kt, "vis": m.vis_mi, "alt": m.altimeter_in,
                "dec": json.dumps(decoded) if decoded else None, "cat": cat,
            },
        )

    store_reports(conn, pid, parsed.reports)

    notify(conn, {
        "type": "product", "id": pid, "nnn": parsed.nnn, "awips": parsed.afos, "office": parsed.office,
        "category": parsed.category, "title": parsed.title, "issued": issued, "received": en.received,
        "priority": en.priority, "vtec": any(s.vtec for s in parsed.segments),
    })
    return sid


def store_reports(conn: Connection, pid: int, reports) -> None:
    for r in reports:
        if r.kind == "TAF" and r.station:
            conn.execute(
                text(
                    """
                    INSERT INTO taf (product_id, station, issued, valid_from, valid_to, raw, decoded)
                    VALUES (:pid, :st, :iss, :vf, :vt, :raw, CAST(:dec AS jsonb))
                    ON CONFLICT (station, issued, raw) DO NOTHING
                    """
                ),
                {"pid": pid, "st": r.station, "iss": r.issued, "vf": r.valid_from, "vt": r.valid_to,
                 "raw": r.raw, "dec": json.dumps(r.decoded)},
            )
        elif r.kind == "PIREP":
            # Placed at the filing station (an FAA id such as BFL), so approx,
            # until satweather.pireps places it by its /OV. A re-decode
            # refreshes the decoded form and hazards, not the placement.
            conn.execute(
                text(
                    """
                    INSERT INTO pirep (product_id, station, observed, urgent, hazards, raw, decoded, geom)
                    VALUES (:pid, :st, :obs, :urg, :hz, :raw, CAST(:dec AS jsonb),
                            (SELECT geom FROM station WHERE faa = :st OR id = :st OR id = 'K' || :st
                             ORDER BY (faa = :st) DESC LIMIT 1))
                    ON CONFLICT (raw) DO UPDATE SET hazards = EXCLUDED.hazards, decoded = EXCLUDED.decoded
                    """
                ),
                {"pid": pid, "st": r.station, "obs": r.issued, "urg": r.urgent, "hz": r.hazards,
                 "raw": r.raw, "dec": json.dumps(r.decoded)},
            )
