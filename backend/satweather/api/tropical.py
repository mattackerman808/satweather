"""Tropical cyclones (NHC/CPHC) and the mesoscale sectors watching them.

Everything comes from what the dish received: forecast/advisories (TCM,
parsed into tc_advisory), the products tied to each storm by its ATCF id,
the tropical weather outlooks, and the GOES-18 meso sectors' footprints
from their projection. No cone: NHC's cone is not in the text.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from satweather.db import get_engine
from satweather.imagery import footprint
from satweather.pireps import destination
from satweather.tropical import BASINS, category, parse_two

router = APIRouter(prefix="/api")

OUTLOOKS = {"TWOAT": "AL", "TWOEP": "EP", "TWOCP": "CP"}
# Quadrant of each 5 degree step: NE, SE, SW, NW.
_STEPS = range(0, 361, 5)


def radii_polygon(lat: float, lon: float, quads: list[int]) -> list[list[float]] | None:
    """The area within a wind radius, from NHC's four quadrant radii
    (nautical miles, NE SE SW NW): the largest extent in each quadrant,
    as NHC defines them."""
    if not any(quads):
        return None
    ring = []
    for b in _STEPS:
        r = quads[min(b // 90, 3)]
        ring.append([lon, lat] if not r else list(reversed(destination(lat, lon, b % 360, r))))
    return ring


def sectors() -> list[dict]:
    """Latest footprint of each GOES-18 mesoscale sector."""
    with get_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT ON (sector) source, sector, scan_time, projection FROM image_frame
            WHERE source = 'GOES-18' AND sector LIKE 'Mesoscale%' AND projection IS NOT NULL
              AND scan_time > now() - interval '3 hours'
            ORDER BY sector, scan_time DESC""")).mappings().all()
    out = []
    for r in rows:
        if fp := footprint(r["projection"]):
            out.append({"source": r["source"], "sector": r["sector"], "scan_time": r["scan_time"], **fp})
    return out


def _inside(lon: float, lat: float, ring: list[list[float]]) -> bool:
    c = False
    for (xa, ya), (xb, yb) in zip(ring, ring[1:]):
        if (ya > lat) != (yb > lat) and lon < (xb - xa) * (lat - ya) / (yb - ya) + xa:
            c = not c
    return c


@router.get("/imagery/sectors")
def imagery_sectors() -> list[dict]:
    return sectors()


def _storm(conn, latest: dict, secs: list[dict]) -> dict:
    sid = latest["storm_id"]
    advs = conn.execute(text(
        "SELECT number, valid, lat, lon, wind_kt, kind, prev FROM tc_advisory WHERE storm_id = :s ORDER BY valid"),
        {"s": sid}).mappings().all()
    # The track: every advisory's center, and the previous positions they
    # report (which fill in the advisories received before the feed began).
    pts: dict[str, dict] = {}
    for a in advs:
        if (pv := a["prev"]) and pv["valid"] not in pts:
            pts[pv["valid"]] = {"valid": pv["valid"], "lat": pv["lat"], "lon": pv["lon"], "wind_kt": None}
        pts[a["valid"].isoformat()] = {"valid": a["valid"].isoformat(), "lat": a["lat"], "lon": a["lon"],
                                       "wind_kt": a["wind_kt"], "category": category(a["wind_kt"]), "kind": a["kind"]}
    products = conn.execute(text("""
        SELECT DISTINCT ON (nnn) id, nnn, awips, title, issued, received FROM text_product
        WHERE storm_id = :s ORDER BY nnn, received DESC"""), {"s": sid}).mappings().all()
    return {
        "storm_id": sid, "basin": BASINS.get(latest["basin"], latest["basin"]), "name": latest["name"],
        "kind": latest["kind"], "number": latest["number"], "issued": latest["issued"], "final": latest["final"],
        "valid": latest["valid"], "lat": latest["lat"], "lon": latest["lon"],
        "move_dir": latest["move_dir"], "move_kt": latest["move_kt"], "pressure_mb": latest["pressure_mb"],
        "wind_kt": latest["wind_kt"], "gust_kt": latest["gust_kt"], "category": category(latest["wind_kt"]),
        "radii": latest["radii"],
        "radii_areas": [{"kt": kt, "ring": ring} for kt in ("34", "50", "64")
                        if (q := latest["radii"].get(kt)) and (ring := radii_polygon(latest["lat"], latest["lon"], q))],
        "track": sorted(pts.values(), key=lambda p: p["valid"]),
        "forecast": [{**f, "category": category(f.get("wind_kt"))} for f in latest["forecast"]],
        "products": [dict(p) for p in sorted(products, key=lambda p: p["received"], reverse=True)],
        "meso": [s["sector"] for s in secs if _inside(latest["lon"], latest["lat"], s["ring"])],
    }


@router.get("/tropical/active")
def active(hours: int = Query(18, ge=1, le=24 * 14)) -> dict:
    """Storms with an advisory in the last `hours` (NHC advises every 6 h;
    a storm whose last advisory says so is marked final)."""
    secs = sectors()
    with get_engine().connect() as conn:
        latest = conn.execute(text("""
            SELECT DISTINCT ON (storm_id) * FROM tc_advisory
            WHERE issued > now() - make_interval(hours => :h)
            ORDER BY storm_id, issued DESC"""), {"h": hours}).mappings().all()
        storms = [_storm(conn, dict(r), secs) for r in latest]
    storms.sort(key=lambda s: (s["final"], -(s["wind_kt"] or 0)))
    return {"storms": storms, "sectors": secs}


@router.get("/tropical/storm/{storm_id}")
def storm(storm_id: str) -> dict:
    with get_engine().connect() as conn:
        latest = conn.execute(text(
            "SELECT * FROM tc_advisory WHERE storm_id = :s ORDER BY issued DESC LIMIT 1"),
            {"s": storm_id.upper()}).mappings().first()
        if latest is None:
            raise HTTPException(404, f"no advisories for {storm_id}")
        out = _storm(conn, dict(latest), sectors())
        out["history"] = [dict(r) for r in conn.execute(text(
            "SELECT number, issued, valid, kind, wind_kt, gust_kt, pressure_mb FROM tc_advisory WHERE storm_id = :s"
            " ORDER BY valid"), {"s": storm_id.upper()}).mappings()]
    return out


@router.get("/tropical/outlooks")
def outlooks() -> list[dict]:
    """The latest tropical weather outlook of each basin, parsed."""
    with get_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT ON (awips) id, awips, issued, received, raw FROM text_product
            WHERE awips = ANY(:a) AND received > now() - interval '2 days'
            ORDER BY awips, received DESC"""), {"a": list(OUTLOOKS)}).mappings().all()
    order = list(OUTLOOKS)
    # "received", not "issued": NHC stamps an outlook with its nominal time
    # ("1100 PM PDT"), which can be after it was sent.
    return [{"basin": BASINS[OUTLOOKS[r["awips"]]], "product_id": r["id"], "awips": r["awips"],
             "received": r["received"], **parse_two(r["raw"])}
            for r in sorted(rows, key=lambda r: order.index(r["awips"]))]
