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
    with get_engine().connect() as conn:
        return zone_at(conn, lon, lat, brief)


def zone_at(conn, lon: float, lat: float, brief: bool = False) -> dict:
    """The zones at a point, the primary zone's latest forecast segment, the
    hazards in effect there and other recent products for it (also used by
    the Forecast tab)."""
    p = {"lon": lon, "lat": lat}
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


_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "district of columbia": "DC", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI",
    "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "puerto rico": "PR", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN",
    "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "guam": "GU",
}
_CODES = set(_STATES.values())


def _split_state(q: str) -> tuple[str, str | None]:
    """"davis, ca" / "davis ca" / "davis, california" -> ("davis", "CA").
    A query that is itself a state name ("west virginia") isn't split."""
    if q.lower() in _STATES:
        return q, None
    if "," in q:
        name, _, st = q.rpartition(",")
        st = st.strip().lower()
        code = st.upper() if st.upper() in _CODES else _STATES.get(st)
        return (name.strip(), code) if code and name.strip() else (q, None)
    words = q.split()
    for n in (2, 1):  # "fort worth new mexico" ... "davis ca"
        if len(words) > n:
            st = " ".join(words[-n:]).lower()
            code = st.upper() if n == 1 and len(st) == 2 and st.upper() in _CODES else _STATES.get(st)
            if code:
                return " ".join(words[:-n]), code
    return q, None


@router.get("/places")
def places(
    q: str = Query(min_length=2, max_length=60),
    near: str | None = Query(None, description="lon,lat to rank by (the map's center)"),
    limit: int = Query(8, ge=1, le=20),
) -> list[dict]:
    """"Forecast for..." search: ZIP codes, US cities and towns (the Census
    places gazetteer, `place`), airports by ICAO/FAA id or name, forecast
    zones by name, and, where the Census list has nothing, cities from the
    zone forecasts' "Including the cities of" lines (Guam, say). A trailing
    state ("davis, ca", "davis ca", "davis, california") narrows the search.

    Ranked: an exact match first, then by distance from `near`, a match at
    the start of the name counting as 300 km closer than one inside it.
    Without `near`, prefix matches first, then bigger places, then the
    shortest name."""
    q = " ".join(q.split())
    term, st = _split_state(q)
    pre, anywhere = _like(term) + "%", "%" + _like(term) + "%"
    cands: list[dict] = []
    with get_engine().connect() as conn:
        # ZIP codes (3-5 digits: a prefix), labelled with the zone they fall in.
        if re.fullmatch(r"\d{3,5}", q):
            for r in conn.execute(text("""
                SELECT z.zip, ST_X(z.geom) AS lon, ST_Y(z.geom) AS lat, u.name AS zone, u.state
                FROM zcta z
                LEFT JOIN LATERAL (SELECT name, state FROM ugc_zone
                                   WHERE kind = 'public' AND ST_Contains(geom, z.geom) LIMIT 1) u ON true
                WHERE z.zip LIKE :pre ORDER BY z.zip LIMIT :n"""), {"pre": q + "%", "n": limit}).mappings():
                cands.append({"kind": "zip", "label": f"ZIP {r['zip']}", "name": r["zip"], "state": r["state"],
                              "detail": ", ".join(filter(None, [r["zone"], r["state"]])) or "ZIP code",
                              "lon": r["lon"], "lat": r["lat"], "exact": r["zip"] == q, "size": 0})
        # Cities and towns (Census), biggest first among equals.
        for r in conn.execute(text("""
            SELECT p.name, p.state, p.aland, ST_X(p.geom) AS lon, ST_Y(p.geom) AS lat, u.name AS zone,
                   lower(p.name) = lower(:t) AS exact
            FROM place p
            LEFT JOIN LATERAL (SELECT name FROM ugc_zone
                               WHERE kind = 'public' AND ST_Contains(geom, p.geom) LIMIT 1) u ON true
            WHERE lower(p.name) LIKE lower(:pre) AND (CAST(:st AS text) IS NULL OR p.state = :st)
            ORDER BY lower(p.name) = lower(:t) DESC, p.aland DESC NULLS LAST LIMIT 40"""),
                {"t": term, "pre": pre, "st": st}).mappings():
            cands.append({"kind": "city", "label": f"{r['name']}, {r['state']}", "name": r["name"],
                          "state": r["state"], "detail": r["zone"] or r["state"], "lon": r["lon"], "lat": r["lat"],
                          "exact": r["exact"], "size": r["aland"] or 0})
        census = bool(cands and any(c["kind"] == "city" for c in cands))
        for r in conn.execute(text("""
            SELECT id, faa, trim(name) AS name, state, country, ST_X(geom) AS lon, ST_Y(geom) AS lat,
                   upper(id) = upper(:q) OR upper(faa) = upper(:q) AS exact
            FROM station
            WHERE upper(id) = upper(:q) OR upper(faa) = upper(:q)
               OR (id ~ '^[A-Z][A-Z0-9]{3}$' AND name ILIKE :any)
            LIMIT 60"""), {"q": term, "any": anywhere}).mappings():
            cands.append({"kind": "station", "label": f"{r['id']} · {(r['name'] or '').title()}", "name": r["name"] or "",
                          "state": r["state"], "detail": ", ".join(filter(None, [r["state"], r["country"]])),
                          "lon": r["lon"], "lat": r["lat"], "exact": r["exact"], "size": 0})
        for r in conn.execute(text("""
            SELECT ugc, name, kind, state, ST_X(p) AS lon, ST_Y(p) AS lat, upper(ugc) = upper(:q) AS exact
            FROM (SELECT ugc, name, kind, state, ST_PointOnSurface(geom) AS p FROM ugc_zone
                  WHERE kind IN ('public', 'marine', 'offshore')
                    AND (name ILIKE :any OR upper(ugc) = upper(:q))) z
            LIMIT 60"""), {"q": term, "any": anywhere}).mappings():
            cands.append({"kind": "zone", "label": r["name"], "name": r["name"], "state": r["state"],
                          "detail": f"{r['ugc']} · {r['kind']} zone", "lon": r["lon"], "lat": r["lat"],
                          "exact": r["exact"], "size": 0})
        # Cities the zone forecasts list, where the Census list had none.
        found: set[tuple[str, str]] = set()
        if not census:
            segs = conn.execute(text("""
                SELECT DISTINCT ON (s.ugcs) s.ugcs, s.body
                FROM product_segment s JOIN text_product p ON p.id = s.product_id
                WHERE p.nnn = 'ZFP' AND p.received > now() - interval '3 days' AND s.body ILIKE :any
                ORDER BY s.ugcs, p.received DESC LIMIT 60"""), {"any": anywhere}).all()
            for ugcs, body in segs:
                if m := _CITIES.search(body):
                    for city in re.split(r",\s*(?:and\s+)?|\s+and\s+", " ".join(m[1].split())):
                        city = city.strip(" .")
                        if term.lower() in city.lower():
                            found.add((city, ugcs[0]))
        if found:
            pts = {r[0]: r for r in conn.execute(text("""
                SELECT ugc, state, ST_X(ST_PointOnSurface(geom)), ST_Y(ST_PointOnSurface(geom)), name
                FROM ugc_zone WHERE kind = 'public' AND ugc = ANY(:u)"""), {"u": sorted({u for _, u in found})})}
            for city, ugc in found:
                if ugc in pts:
                    _, state, lon, lat, zname = pts[ugc]
                    cands.append({"kind": "city", "label": f"{city}, {state}" if state else city, "name": city,
                                  "state": state, "detail": zname, "lon": lon, "lat": lat, "exact": False, "size": 0})
    if st:
        cands = [c for c in cands if c["kind"] == "zip" or c.get("state") == st]
    center = None
    if near:
        try:
            center = tuple(float(x) for x in near.split(","))[:2]
        except ValueError:
            center = None

    def score(c: dict) -> tuple:
        prefix = c["name"].lower().startswith(term.lower())
        if center:
            return (not c["exact"], _km(center, (c["lon"], c["lat"])) - (300 if prefix else 0))
        return (not c["exact"], not prefix, -c["size"], len(c["label"]))

    seen, out = set(), []
    for c in sorted(cands, key=score):
        key = (c["label"].lower(), round(c["lon"], 2), round(c["lat"], 2))
        if key not in seen:
            seen.add(key)
            out.append({k: c[k] for k in ("kind", "label", "detail", "lon", "lat")})
    return out[:limit]
