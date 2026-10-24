"""Stations (latest METAR + TAF) and PIREPs for the map.

Decoding is Tachbase's (satweather.wxdecode), stored at ingest. The one
thing computed per request is "what does the TAF say right now", because
"now" moves: the TAF is re-decoded with at=<current DDHHMMZ>.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from satweather import wxdecode
from satweather.db import get_engine

router = APIRouter(prefix="/api")


def _now_ddhhmm() -> str:
    return datetime.now(UTC).strftime("%d%H%MZ")


@router.get("/stations/latest")
def latest(hours: float = Query(3, gt=0, le=24)) -> dict:
    """Newest METAR per station within `hours`, as GeoJSON points."""
    sql = """
        SELECT DISTINCT ON (m.station) m.station, m.valid, m.kind, m.raw, m.flight_category,
               m.temp_c, m.dewpoint_c, m.wind_dir, m.wind_kt, m.gust_kt, m.vis_mi,
               s.name, s.state, s.country, ST_X(s.geom) AS lon, ST_Y(s.geom) AS lat,
               EXISTS (SELECT 1 FROM taf t WHERE t.station = m.station
                       AND t.valid_to > now()) AS has_taf
        FROM metar m JOIN station s ON s.id = m.station
        WHERE m.valid > now() - make_interval(secs => :secs)
        ORDER BY m.station, m.valid DESC
    """
    feats = []
    with get_engine().connect() as conn:
        for r in conn.execute(text(sql), {"secs": hours * 3600}).mappings():
            props = {k: r[k] for k in ("station", "kind", "raw", "flight_category", "temp_c", "dewpoint_c",
                                       "wind_dir", "wind_kt", "gust_kt", "vis_mi", "name", "state",
                                       "country", "has_taf")}
            props["valid"] = r["valid"].isoformat()
            props["flight_category"] = props["flight_category"] or "UNK"
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                          "properties": props})
    return {"type": "FeatureCollection", "features": feats}


@router.get("/stations/{sid}")
def station(sid: str) -> dict:
    sid = sid.upper()
    with get_engine().connect() as conn:
        st = conn.execute(
            text("SELECT id, icao, faa, name, state, country, elev_m, ST_X(geom) lon, ST_Y(geom) lat "
                 "FROM station WHERE id = :s"),
            {"s": sid},
        ).mappings().first()
        metars = conn.execute(
            text(
                """
                SELECT valid, kind, raw, flight_category, decoded, temp_c, dewpoint_c, wind_dir, wind_kt,
                       gust_kt, vis_mi, altimeter_in, product_id
                FROM metar WHERE station = :s AND valid > now() - interval '24 hours'
                ORDER BY valid DESC LIMIT 30
                """
            ),
            {"s": sid},
        ).mappings().all()
        taf = conn.execute(
            text(
                """
                SELECT issued, valid_from, valid_to, raw, product_id FROM taf
                WHERE station = :s ORDER BY issued DESC NULLS LAST LIMIT 1
                """
            ),
            {"s": sid},
        ).mappings().first()
    if st is None and not metars and taf is None:
        raise HTTPException(404, "unknown station")
    out = {"station": dict(st) if st else {"id": sid}, "metars": [dict(m) for m in metars], "taf": None}
    if taf:
        live = taf["valid_to"] is None or taf["valid_to"] > datetime.now(UTC)
        out["taf"] = {**dict(taf), "current": live,
                      "decoded": wxdecode.decode_taf(taf["raw"], at=_now_ddhhmm() if live else "")}
    return out


@router.get("/pireps/recent")
def pireps(hours: float = Query(3, gt=0, le=24)) -> dict:
    sql = """
        SELECT id, station, observed, urgent, hazards, raw, decoded, approx,
               ST_X(geom) lon, ST_Y(geom) lat
        FROM pirep WHERE geom IS NOT NULL AND observed > now() - make_interval(secs => :secs)
        ORDER BY observed DESC
    """
    feats = []
    with get_engine().connect() as conn:
        for r in conn.execute(text(sql), {"secs": hours * 3600}).mappings():
            d = r["decoded"]
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                "properties": {
                    "id": r["id"], "station": r["station"], "observed": r["observed"].isoformat()
                    if r["observed"] else None, "urgent": r["urgent"], "hazards": r["hazards"],
                    "hazard": "TB" if "TB" in r["hazards"] else "IC" if "IC" in r["hazards"]
                    else r["hazards"][0] if r["hazards"] else "",
                    "raw": r["raw"], "approx": r["approx"],
                    "decoded": json.dumps(d.get("decoded", [])),
                },
            })
    return {"type": "FeatureCollection", "features": feats}
