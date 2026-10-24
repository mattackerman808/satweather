"""Active VTEC events as GeoJSON for the map.

An event is active for a UGC while its latest action there is not
CAN/EXP/UPG and it has not ended. Storm-based events (SV.W, TO.W, FF.W,
...) are drawn with their warning polygon; zone-based ones (WS.W, HT.Y,
FW.W ...) with the union of their active zones' shapes. Fire weather
events use fire-zone shapes, marine UGCs marine/offshore shapes.

The union over many zones is the expensive part, so the collection is
cached for a short time per process.
"""

from __future__ import annotations

import json
import time
from functools import lru_cache

from fastapi import APIRouter
from sqlalchemy import text

from satweather.db import get_engine

router = APIRouter(prefix="/api")

CACHE_S = 30
_cache: tuple[float, dict] | None = None


@lru_cache(maxsize=1)
def _vtec_tables():
    from pyiem.nws.vtec import NWS_COLORS, VTEC_PHENOMENA, VTEC_SIGNIFICANCE

    return NWS_COLORS, VTEC_PHENOMENA, VTEC_SIGNIFICANCE


def vtec_label(ph: str, sig: str) -> dict:
    colors, phen, sigs = _vtec_tables()
    return {
        "name": f"{phen.get(ph, ph)} {sigs.get(sig, sig)}".strip(),
        "color": colors.get(f"{ph}.{sig}", "#C0C0C0"),
    }


SQL = """
WITH active AS (
    SELECT u.office, u.phenomena, u.significance, u.etn, u.year,
           array_agg(u.ugc ORDER BY u.ugc) AS ugcs, max(u.ends) AS ends
    FROM vtec_ugc_state u
    WHERE u.action NOT IN ('CAN', 'EXP', 'UPG')
      AND (u.ends IS NULL OR u.ends > now())
    GROUP BY 1, 2, 3, 4, 5
)
SELECT a.office, a.phenomena, a.significance, a.etn, a.year, a.ugcs, a.ends,
       e.action, e.begins, e.product_id, e.issued, p.title,
       ST_AsGeoJSON(COALESCE(e.polygon, (
           SELECT ST_Multi(ST_Union(z.geom)) FROM ugc_zone z
           WHERE z.ugc = ANY(a.ugcs)
             AND z.kind = CASE
                 WHEN a.phenomena = 'FW' THEN 'fire'
                 WHEN substr(z.ugc, 3, 1) = 'C' THEN 'county'
                 WHEN z.kind IN ('marine', 'offshore') THEN z.kind
                 ELSE 'public' END
       )), 4) AS geom,
       e.polygon IS NOT NULL AS storm_based
FROM active a
JOIN vtec_event_state e USING (office, phenomena, significance, etn, year)
JOIN text_product p ON p.id = e.product_id
"""


@router.get("/warnings/active")
def active_warnings() -> dict:
    global _cache
    if _cache and time.time() - _cache[0] < CACHE_S:
        return _cache[1]
    features = []
    with get_engine().connect() as conn:
        for r in conn.execute(text(SQL)).mappings():
            label = vtec_label(r["phenomena"], r["significance"])
            features.append(
                {
                    "type": "Feature",
                    "geometry": json.loads(r["geom"]) if r["geom"] else None,
                    "properties": {
                        "key": f"{r['office']}.{r['phenomena']}.{r['significance']}.{r['etn']:04d}.{r['year']}",
                        "office": r["office"],
                        "phenomena": r["phenomena"],
                        "significance": r["significance"],
                        "etn": r["etn"],
                        "action": r["action"],
                        "begins": r["begins"].isoformat() if r["begins"] else None,
                        "ends": r["ends"].isoformat() if r["ends"] else None,
                        "issued": r["issued"].isoformat(),
                        "ugcs": r["ugcs"],
                        "product_id": r["product_id"],
                        "title": r["title"],
                        "storm_based": r["storm_based"],
                        **label,
                    },
                }
            )
    # Draw advisories under watches under warnings.
    rank = {"Y": 0, "S": 0, "A": 1, "W": 2}
    features.sort(key=lambda f: rank.get(f["properties"]["significance"], 0))
    fc = {"type": "FeatureCollection", "features": features}
    _cache = (time.time(), fc)
    return fc
