"""NWS forecast offices: each one's area as GeoJSON, and its newest Area
Forecast Discussion.

For clients that need "which office covers this point" without a call per
point: Tachbase stores the areas and picks an airport's AFD from them. An
office's area is the union of its public forecast zones, simplified to
~1 km; its name comes from its newest AFD's "National Weather Service ..."
line. The union is the slow part (~4 s), so the collection is cached per
process for CACHE_S.
"""

from __future__ import annotations

import json
import re
import time

from fastapi import APIRouter, Query
from sqlalchemy import text

from satweather.db import get_engine

router = APIRouter(prefix="/api")

CACHE_S = 6 * 3600
_cache: tuple[float, dict] | None = None
_NAME = re.compile(r"^National Weather Service (.+?)\s*$", re.M)

AREAS = """
WITH area AS (
    SELECT cwa, ST_Multi(ST_SimplifyPreserveTopology(ST_Union(geom), 0.01)) AS geom
    FROM ugc_zone WHERE kind = 'public' AND cwa IS NOT NULL GROUP BY cwa
)
SELECT a.cwa, ST_AsGeoJSON(a.geom, 4) AS geom, n.head
FROM area a
LEFT JOIN LATERAL (
    SELECT left(p.raw, 400) AS head FROM text_product p
    WHERE p.awips = 'AFD' || a.cwa ORDER BY p.received DESC LIMIT 1
) n ON true
ORDER BY a.cwa
"""


@router.get("/offices")
def offices() -> dict:
    """One feature per office: `office` is the 3-letter id (MTR), the one
    AWIPS ids use (AFDMTR); `name` is null until it has sent an AFD."""
    global _cache
    if _cache and time.time() - _cache[0] < CACHE_S:
        return _cache[1]
    features = []
    with get_engine().connect() as conn:
        for r in conn.execute(text(AREAS)).mappings():
            name = _NAME.search(r["head"] or "")
            features.append({
                "type": "Feature",
                "geometry": json.loads(r["geom"]),
                "properties": {"office": r["cwa"], "name": name[1] if name else None},
            })
    fc = {"type": "FeatureCollection", "features": features}
    _cache = (time.time(), fc)
    return fc


@router.get("/offices/afd")
def latest_afds(days: int = Query(3, ge=1, le=14)) -> dict:
    """Each office's newest AFD (id and times only; the text is
    /api/products/{id}), so a client can fetch just the ones that changed."""
    with get_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT ON (awips) substr(awips, 4) AS office, id AS product_id, issued, received
            FROM text_product
            WHERE nnn = 'AFD' AND received > now() - make_interval(days => :d)
            ORDER BY awips, received DESC"""), {"d": days}).mappings().all()
    return {"items": [dict(r) for r in rows]}
