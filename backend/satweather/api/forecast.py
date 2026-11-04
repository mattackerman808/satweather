"""The Forecast tab: a 7-day forecast for a place from the nearest Point
Forecast Matrix point, with the zone's forecast text, hazards, the office's
AFD and the national extended discussions; and the LLM's plain-English
outlook (days 8-10 from CPC) and AFD rewrite.

The numbers are always the PFM's (pfm.py, parsed at ingest). The LLM only
words things (forecast_llm.py, llm.py), and the tab works without it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from satweather import forecast_llm as fl
from satweather import llm, pfm
from satweather.api.zones import zone_at
from satweather.db import get_engine

router = APIRouter(prefix="/api")

MAX_KM = 100  # farther than this, there is no forecast point "for" a place
NATIONAL = ("PMDEPD", "PMDMRD", "PMDTHR")
_PT = "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)"


def _now() -> datetime:  # tests pin this to the fixtures' time
    return datetime.now(UTC)


def _points(conn, lon: float, lat: float) -> tuple[dict | None, dict | None, float | None]:
    """(newest issuance, the one before, km) of the nearest PFM point."""
    near = conn.execute(text(f"""
        SELECT office, name, ST_Distance(geom::geography, {_PT}::geography) / 1000 AS km
        FROM pfm_point WHERE issued > :now - interval '2 days'
        ORDER BY geom <-> {_PT} LIMIT 1"""), {"lon": lon, "lat": lat, "now": _now()}).mappings().first()
    if near is None or near["km"] > MAX_KM:
        return None, None, None
    rows = conn.execute(text("""
        SELECT id, product_id, office, ugc, name, ST_X(geom) AS lon, ST_Y(geom) AS lat, elev_ft, issued, expires,
               utc_offset_h, series, twelve
        FROM pfm_point WHERE office = :o AND name = :n ORDER BY issued DESC LIMIT 2"""),
        {"o": near["office"], "n": near["name"]}).mappings().all()
    cur = dict(rows[0]) if rows else None
    prev = dict(rows[1]) if len(rows) > 1 else None
    return cur, prev, round(near["km"], 1)


def _latest(conn, awips: str) -> dict | None:
    r = conn.execute(text("""SELECT id, awips, title, issued, received FROM text_product
                             WHERE awips = :a ORDER BY received DESC LIMIT 1"""), {"a": awips}).mappings().first()
    return dict(r) if r else None


def _gather(conn, lon: float, lat: float) -> dict:
    z = zone_at(conn, lon, lat)
    cur, prev, km = _points(conn, lon, lat)
    out: dict = {"lon": lon, "lat": lat, "zone": z["zone"], "zones": z["zones"], "forecast": z["forecast"],
                 "hazards": z["hazards"], "point": None, "daily": [], "hours": {}, "changes": [],
                 "afd": None, "national": {}, "llm": {"enabled": llm.enabled()}}
    if cur:
        off = cur["utc_offset_h"]
        today = (_now() + timedelta(hours=off)).date().isoformat()
        rows = [r for r in pfm.daily(cur) if r["date"] >= today]
        out["daily"] = rows
        out["hours"] = {r["date"]: pfm.hourly(cur, r["date"]) for r in rows}
        if prev:
            out["changes"] = [c for c in pfm.diff(prev, cur) if c["date"] >= today]
        out["point"] = {**{k: cur[k] for k in ("product_id", "office", "ugc", "name", "lon", "lat", "elev_ft",
                                                "issued", "expires", "utc_offset_h", "series", "twelve")},
                        "distance_km": km, "prev_product_id": prev["product_id"] if prev else None,
                        "prev_issued": prev["issued"] if prev else None}
    cwa = (z["zone"] or {}).get("cwa") or (cur or {}).get("office", "")[-3:] or None
    if cwa:
        out["afd"] = _latest(conn, f"AFD{cwa}")
    out["national"] = {a: _latest(conn, a) for a in NATIONAL}
    return out


@router.get("/forecast")
def forecast(lon: float = Query(ge=-180, le=180), lat: float = Query(ge=-90, le=90)) -> dict:
    with get_engine().connect() as conn:
        return pfm.jsonable(_gather(conn, lon, lat))


def _raw(conn, pid: int | None) -> str | None:
    if not pid:
        return None
    return conn.execute(text("SELECT raw FROM text_product WHERE id = :i"), {"i": pid}).scalar()


@router.get("/forecast/outlook")
def outlook(lon: float = Query(ge=-180, le=180), lat: float = Query(ge=-90, le=90)) -> dict:
    """The LLM's plain-English outlook for the place:
    {status: ready|pending|unavailable|failed|none, content, model, created}."""
    if not llm.enabled():
        return {"status": "unavailable", "content": None}
    with get_engine().connect() as conn:
        g = _gather(conn, lon, lat)
        if not g["point"] and not g["forecast"]:
            return {"status": "none", "content": None}
        nat = g["national"]
        afd_id = (g["afd"] or {}).get("id")
        zfp_id = (g["forecast"] or {}).get("product_id")
        p = g["point"] or {}
        today = (_now() + timedelta(hours=p.get("utc_offset_h", 0))).date()
        later = fl.later_dates(g["daily"], today)
        key = f"{(g['zone'] or {}).get('ugc', '-')}|{p.get('office', '-')}|{p.get('name', '-')}"
        inputs = [zfp_id, p.get("product_id"), p.get("prev_product_id"), afd_id,
                  [(nat.get(a) or {}).get("id") for a in NATIONAL], later]
        texts = {"afd": _raw(conn, afd_id), "wpc": _raw(conn, (nat.get("PMDEPD") or {}).get("id")),
                 "cpc": _raw(conn, (nat.get("PMDMRD") or {}).get("id"))}

    def build():
        zone = g["zone"] or {}
        place = ", ".join(x for x in (zone.get("name"), zone.get("state")) if x) or p.get("name", "")
        if p:
            place += f"; forecast point {p['name']} ({p['distance_km']} km away)"
        table = fl.daily_table(g["daily"])
        zfp = (g["forecast"] or {}).get("body")
        afd = fl.afd_sections(texts["afd"]) if texts["afd"] else None
        user = fl.outlook_prompt(place, zone.get("cwa") or p.get("office"), zfp, table, g["changes"], afd,
                                 texts["wpc"], texts["cpc"], later)
        return fl.OUTLOOK_SYSTEM, user, llm.numbers_in(user)

    return pfm.jsonable(llm.cached("outlook", key, inputs, build, fl.validate_outlook(later)))


@router.get("/products/{pid}/plain")
def plain(pid: int) -> dict:
    """An AFD rewritten in plain English with a glossary (LLM; cached per product)."""
    with get_engine().connect() as conn:
        r = conn.execute(text("SELECT nnn, raw FROM text_product WHERE id = :i"), {"i": pid}).mappings().first()
    if r is None:
        raise HTTPException(404, "no such product")
    if r["nnn"] != "AFD":
        raise HTTPException(400, "plain English is only for Area Forecast Discussions")
    if not llm.enabled():
        return {"status": "unavailable", "content": None}
    raw = r["raw"]

    def build():
        return fl.AFD_SYSTEM, raw, llm.numbers_in(raw)

    return pfm.jsonable(llm.cached("afd", str(pid), [pid], build, fl.validate_afd, max_tokens=4000))
