"""Mesoscale discussions in effect, as GeoJSON for the map: SPC's MCDs
(SWOMCD: severe weather, winter weather, watch likelihood) and WPC's
MPDs (FFGMPD: heavy rain, flash flooding).

Each is one segment with a LAT...LON polygon and a "Valid ddhhmmZ -
ddhhmmZ" window; the window's end is the segment's UGC expiry, which is
what "in effect" is measured against.
"""

from __future__ import annotations

import json
import re

from fastapi import APIRouter
from sqlalchemy import text

from satweather.db import get_engine

router = APIRouter(prefix="/api")

KINDS = {"SWOMCD": ("MCD", "#4fb3ff"), "FFGMPD": ("MPD", "#3ccf8e")}
_NUMBER = re.compile(r"Discussion\s+(\d+)", re.I)
_CONCERNING = re.compile(r"^Concerning\.\.\.(.+)$", re.M)


@router.get("/discussions/active")
def active() -> dict:
    with get_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT p.id, p.awips, p.title, p.issued, s.ugc_expire, s.body,
                   ST_AsGeoJSON(s.polygon, 4) AS geom
            FROM product_segment s JOIN text_product p ON p.id = s.product_id
            WHERE p.awips = ANY(:awips) AND s.polygon IS NOT NULL
              AND coalesce(s.ugc_expire, p.issued + interval '3 hours') > now()
            ORDER BY p.issued"""), {"awips": list(KINDS)}).mappings().all()
    features = []
    for r in rows:
        kind, color = KINDS[r["awips"]]
        num = _NUMBER.search(r["title"] or "") or _NUMBER.search(r["body"])
        concerning = _CONCERNING.search(r["body"])
        features.append({
            "type": "Feature",
            "geometry": json.loads(r["geom"]),
            "properties": {
                "product_id": r["id"], "kind": kind, "color": color,
                "number": int(num[1]) if num else None,
                "label": f"{kind} {num[1]}" if num else kind,
                "concerning": concerning[1].strip() if concerning else None,
                "issued": r["issued"].isoformat() if r["issued"] else None,
                "expires": r["ugc_expire"].isoformat() if r["ugc_expire"] else None,
            },
        })
    return {"type": "FeatureCollection", "features": features}
