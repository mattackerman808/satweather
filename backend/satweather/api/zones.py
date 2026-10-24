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
def at(lon: float = Query(ge=-180, le=180), lat: float = Query(ge=-90, le=90)) -> dict:
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
