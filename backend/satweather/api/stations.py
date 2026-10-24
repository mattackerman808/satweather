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
from satweather.airport_classes import CLASS_B, MAJOR_HUBS, tier
from satweather.db import get_engine

router = APIRouter(prefix="/api")


def _now_ddhhmm() -> str:
    return datetime.now(UTC).strftime("%d%H%MZ")


@router.get("/stations/latest")
def latest(
    bbox: str | None = Query(None, description="minLon,minLat,maxLon,maxLat (minLon > maxLon crosses 180°)"),
    zoom: float = Query(10, ge=0, le=24),
    hours: float = Query(3, gt=0, le=24),
) -> dict:
    """Newest METAR per station in view, thinned by zoom the way Tachbase
    does it (satweather.airport_classes): only important stations when
    zoomed out, everything from zoom 7. Properties are only what a map
    dot needs; the detail panel fetches the rest."""
    envelopes = []
    large = True
    if bbox:
        try:
            w, s_, e, n = (float(x) for x in bbox.split(","))
        except ValueError:
            raise HTTPException(400, "bbox must be minLon,minLat,maxLon,maxLat")
        width = e - w if e >= w else 360 + e - w
        large = width > 50 or n - s_ > 30
        envelopes = [(w, s_, 180, n), (-180, s_, e, n)] if w > e else [(w, s_, e, n)]
    classes, max_pri, limit = tier(zoom, large)
    where = ["(:all OR s.id = ANY(:cls) OR COALESCE(s.priority, 9) <= :maxp)"]
    params: dict = {"all": classes is None, "cls": sorted(classes or ()), "maxp": max_pri,
                    "hubs": sorted(MAJOR_HUBS | CLASS_B), "secs": hours * 3600, "limit": limit}
    if envelopes:
        ors = []
        for i, (a, b, c, d) in enumerate(envelopes):
            ors.append(f"s.geom && ST_MakeEnvelope(:w{i}, :s{i}, :e{i}, :n{i}, 4326)")
            params.update({f"w{i}": a, f"s{i}": b, f"e{i}": c, f"n{i}": d})
        where.append("(" + " OR ".join(ors) + ")")
    sql = f"""
        SELECT s.id AS station, ST_X(s.geom) AS lon, ST_Y(s.geom) AS lat,
               m.flight_category, m.temp_c, m.wind_dir, m.wind_kt, m.gust_kt
        FROM station s
        JOIN LATERAL (
            SELECT flight_category, temp_c, wind_dir, wind_kt, gust_kt FROM metar
            WHERE metar.station = s.id AND metar.valid > now() - make_interval(secs => :secs)
            ORDER BY valid DESC LIMIT 1
        ) m ON true
        WHERE {" AND ".join(where)}
        ORDER BY (s.id = ANY(:hubs)) DESC, COALESCE(s.priority, 9), s.id
        LIMIT :limit
    """
    feats = []
    with get_engine().connect() as conn:
        for r in conn.execute(text(sql), params).mappings():
            props = {"station": r["station"], "flight_category": r["flight_category"] or "UNK"}
            for k in ("temp_c", "wind_dir", "wind_kt", "gust_kt"):
                if r[k] is not None:
                    props[k] = r[k]
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                          "properties": props})
    return {"type": "FeatureCollection", "features": feats, "limit": limit, "truncated": len(feats) >= limit}


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
