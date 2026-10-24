"""What applies at a point on the map: its zones, the latest forecast for
them, other recent products naming them, and the hazards in effect there.

A point lies in several zones at once: a public forecast zone (or a
marine / offshore one at sea), a county, a fire weather zone (usually the
same code as the public one). Forecasts are issued per zone, hazards per
zone or per county. Storm-based warnings (SV.W, TO.W, FF.W, ...) are
issued per county but only cover their polygon, so they count only when
the point is inside it.
"""

from __future__ import annotations

import json
import math
import re

from fastapi import APIRouter, Query
from sqlalchemy import text

from satweather.api.warnings import vtec_label
from satweather.catalog import describe
from satweather.db import get_engine

router = APIRouter(prefix="/api")

# The zone whose forecast the panel leads with, and the products that are
# that kind of zone's forecast.
PRIMARY = ("public", "marine", "offshore")
FORECAST_NNN = {"public": ["ZFP"], "marine": ["CWF", "NSH"], "offshore": ["OFF"]}

_PT = "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)"


@router.get("/zones/at")
def at(lon: float = Query(ge=-180, le=180), lat: float = Query(ge=-90, le=90), brief: bool = False) -> dict:
    """`brief`: just the zone and its outline (the map's hover preview)."""
    p = {"lon": lon, "lat": lat}
    with get_engine().connect() as conn:
        zones = conn.execute(text(f"""
            SELECT kind, ugc, name, state, cwa,
                   ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom, 0.002), 4) AS geom
            FROM ugc_zone WHERE ST_Contains(geom, {_PT})"""), p).mappings().all()
        primary = next((z for k in PRIMARY for z in zones if z["kind"] == k), None)
        out: dict = {
            "lon": lon, "lat": lat,
            "zones": [{k: z[k] for k in ("kind", "ugc", "name", "state", "cwa")} for z in zones],
            "zone": None, "forecast": None, "hazards": [], "products": [],
        }
        if primary is None:
            return out
        out["zone"] = {k: primary[k] for k in ("kind", "ugc", "name", "state", "cwa")}
        out["zone"]["geometry"] = json.loads(primary["geom"])
        if brief:
            return out
        ugcs = sorted({z["ugc"] for z in zones})

        f = conn.execute(text("""
            SELECT p.id AS product_id, p.nnn, p.awips, p.office, p.title, p.issued, p.received,
                   s.ugc_expire, s.headlines, s.body
            FROM product_segment s JOIN text_product p ON p.id = s.product_id
            WHERE :ugc = ANY(s.ugcs) AND p.nnn = ANY(:nnn)
            ORDER BY coalesce(p.issued, p.received) DESC LIMIT 1"""),
            {"ugc": primary["ugc"], "nnn": FORECAST_NNN[primary["kind"]]}).mappings().first()
        if f:
            out["forecast"] = dict(f)

        out["hazards"] = [
            {**dict(r), **vtec_label(r["phenomena"], r["significance"])}
            for r in conn.execute(text(f"""
                SELECT DISTINCT ON (e.office, e.phenomena, e.significance, e.etn, e.year)
                       e.office, e.phenomena, e.significance, e.etn, e.action, e.begins,
                       u.ends, e.product_id, e.issued, e.polygon IS NOT NULL AS storm_based
                FROM vtec_ugc_state u
                JOIN vtec_event_state e USING (office, phenomena, significance, etn, year)
                WHERE u.ugc = ANY(:ugcs) AND u.action NOT IN ('CAN', 'EXP', 'UPG')
                  AND (u.ends IS NULL OR u.ends > now())
                  AND (e.polygon IS NULL OR ST_Contains(e.polygon, {_PT}))
                ORDER BY e.office, e.phenomena, e.significance, e.etn, e.year, u.ends DESC NULLS FIRST"""),
                {**p, "ugcs": ugcs}).mappings()
        ]

        # Newest of each other product type naming one of these zones (the
        # forecast's own type and the hazards' products are shown above). A
        # segment with a polygon (storm-based) counts only if it covers the
        # point, as for hazards.
        rows = conn.execute(text(f"""
            SELECT DISTINCT ON (p.nnn) p.id, p.nnn, p.awips, p.office, p.title, p.issued, p.received
            FROM product_segment s JOIN text_product p ON p.id = s.product_id
            WHERE s.ugcs && :ugcs AND p.received > now() - interval '2 days'
              AND (s.polygon IS NULL OR ST_Contains(s.polygon, {_PT}))
              AND NOT (p.id = ANY(:skip)) AND NOT (p.nnn = ANY(:skip_nnn))
            ORDER BY p.nnn, p.received DESC"""),
            {**p, "ugcs": ugcs, "skip": [h["product_id"] for h in out["hazards"]],
             "skip_nnn": [f["nnn"]] if f else []},
        ).mappings().all()
        out["products"] = sorted(({**dict(r), "type_name": describe(r["nnn"])[0]} for r in rows),
                                 key=lambda r: r["received"], reverse=True)
    return out


_CITIES = re.compile(r"Including the cities of\s+(.+?)(?:\n\s*\n|\n\d{3,4} [AP]M)", re.S | re.I)


def _like(q: str) -> str:
    return re.sub(r"([\\%_])", r"\\\1", q)


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    (lon1, lat1), (lon2, lat2) = (map(math.radians, a)), (map(math.radians, b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


@router.get("/places")
def places(
    q: str = Query(min_length=2, max_length=60),
    near: str | None = Query(None, description="lon,lat to rank by (the map's center)"),
    limit: int = Query(8, ge=1, le=20),
) -> list[dict]:
    """"Forecast for..." search: airports by ICAO/FAA id or name, forecast
    zones by name, and cities by name from the zone forecasts' "Including
    the cities of" lines (not every office lists them). Each result has a
    point inside the zone whose forecast it opens.

    Ranked: an exact id first, then by distance from `near`, a match at the
    start of the name counting as 300 km closer than one inside it. Without
    `near`, prefix matches first, shortest name first."""
    q = q.strip()
    pre, anywhere = _like(q) + "%", "%" + _like(q) + "%"
    cands: list[dict] = []
    with get_engine().connect() as conn:
        for r in conn.execute(text("""
            SELECT id, faa, trim(name) AS name, state, country, ST_X(geom) AS lon, ST_Y(geom) AS lat,
                   upper(id) = upper(:q) OR upper(faa) = upper(:q) AS exact
            FROM station
            WHERE upper(id) = upper(:q) OR upper(faa) = upper(:q)
               OR (id ~ '^[A-Z][A-Z0-9]{3}$' AND name ILIKE :any)
            LIMIT 60"""), {"q": q, "any": anywhere}).mappings():
            cands.append({"kind": "station", "label": f"{r['id']} · {(r['name'] or '').title()}", "name": r["name"] or "",
                          "detail": ", ".join(filter(None, [r["state"], r["country"]])),
                          "lon": r["lon"], "lat": r["lat"], "exact": r["exact"]})
        for r in conn.execute(text("""
            SELECT ugc, name, kind, ST_X(p) AS lon, ST_Y(p) AS lat, upper(ugc) = upper(:q) AS exact
            FROM (SELECT ugc, name, kind, ST_PointOnSurface(geom) AS p FROM ugc_zone
                  WHERE kind IN ('public', 'marine', 'offshore')
                    AND (name ILIKE :any OR upper(ugc) = upper(:q))) z
            LIMIT 60"""), {"q": q, "any": anywhere}).mappings():
            cands.append({"kind": "zone", "label": r["name"], "name": r["name"],
                          "detail": f"{r['ugc']} · {r['kind']} zone", "lon": r["lon"], "lat": r["lat"],
                          "exact": r["exact"]})
        # Cities: newest ZFP segment per zone set that names the city.
        segs = conn.execute(text("""
            SELECT DISTINCT ON (s.ugcs) s.ugcs, s.body
            FROM product_segment s JOIN text_product p ON p.id = s.product_id
            WHERE p.nnn = 'ZFP' AND p.received > now() - interval '3 days' AND s.body ILIKE :any
            ORDER BY s.ugcs, p.received DESC LIMIT 60"""), {"any": anywhere}).all()
        found: set[tuple[str, str]] = set()
        for ugcs, body in segs:
            if m := _CITIES.search(body):
                for city in re.split(r",\s*(?:and\s+)?|\s+and\s+", " ".join(m[1].split())):
                    city = city.strip(" .")
                    if q.lower() in city.lower():
                        found.add((city, ugcs[0]))
        if found:
            pts = {r[0]: r for r in conn.execute(text("""
                SELECT ugc, state, ST_X(ST_PointOnSurface(geom)), ST_Y(ST_PointOnSurface(geom)), name
                FROM ugc_zone WHERE kind = 'public' AND ugc = ANY(:u)"""), {"u": sorted({u for _, u in found})})}
            for city, ugc in found:
                if ugc in pts:
                    _, state, lon, lat, zname = pts[ugc]
                    cands.append({"kind": "city", "label": f"{city}, {state}" if state else city, "name": city,
                                  "detail": zname, "lon": lon, "lat": lat, "exact": False})
    center = None
    if near:
        try:
            center = tuple(float(x) for x in near.split(","))[:2]
        except ValueError:
            center = None

    def score(c: dict) -> tuple:
        prefix = c["name"].lower().startswith(q.lower())
        if center:
            return (not c["exact"], _km(center, (c["lon"], c["lat"])) - (300 if prefix else 0))
        return (not c["exact"], not prefix, len(c["label"]))

    seen, out = set(), []
    for c in sorted(cands, key=score):
        key = (c["label"].lower(), round(c["lon"], 2), round(c["lat"], 2))
        if key not in seen:
            seen.add(key)
            out.append({k: c[k] for k in ("kind", "label", "detail", "lon", "lat")})
    return out[:limit]
