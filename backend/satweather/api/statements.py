"""Statements in effect, as GeoJSON: Special Weather Statements with a
polygon (strong storms just below warning criteria) and Airport Weather
Warnings (thunderstorms, wind, fog, ... at one airport).

Neither carries VTEC, so neither is in /api/warnings/active. A statement is
in effect until its segment's UGC expiry (an hour after issue when it has
none). Airport Weather Warnings are reissued per airport (AWWBZN, AWWIWA),
so only the newest one for an airport counts, and a cancellation ends it.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta

from fastapi import APIRouter
from sqlalchemy import text

from satweather.db import get_engine

router = APIRouter(prefix="/api")

KINDS = ("SPS", "AWW")
# How a cancelling AWW reads; an active one says "Until noon MDT", not "expire".
_CANCELLED = re.compile(r"\bcancell?ed\b|\b(has|have|is) expired\b|\bno longer in effect\b", re.I)

# One row per SPS segment, or per airport for AWW (its newest product's
# segment that names zones: the trailing segment of an AWW is empty).
SQL = """
SELECT DISTINCT ON (CASE WHEN p.nnn = 'AWW' THEN p.awips ELSE p.id::text || '.' || s.seq END)
       p.id, p.nnn, p.awips, p.office, p.issued, p.received, s.seq, s.ugc_expire, s.headlines, s.body,
       ST_AsGeoJSON(s.polygon, 4) AS geom
FROM product_segment s JOIN text_product p ON p.id = s.product_id
WHERE p.nnn = ANY(:kinds) AND p.received > now() - interval '1 day' AND cardinality(s.ugcs) > 0
ORDER BY (CASE WHEN p.nnn = 'AWW' THEN p.awips ELSE p.id::text || '.' || s.seq END), p.received DESC, s.seq
"""


@router.get("/statements/active")
def active() -> dict:
    with get_engine().connect() as conn:
        now = conn.execute(text("SELECT now()")).scalar()
        rows = conn.execute(text(SQL), {"kinds": list(KINDS)}).mappings().all()
    features = []
    for r in rows:
        expires = r["ugc_expire"] or ((r["issued"] or r["received"]) + timedelta(hours=1))
        headline = (r["headlines"] or [None])[0]
        if expires <= now or not r["geom"]:
            continue
        if r["nnn"] == "AWW" and (_CANCELLED.search(headline or "") or _CANCELLED.search(r["body"][:600])):
            continue
        features.append({
            "type": "Feature",
            "geometry": json.loads(r["geom"]),
            "properties": {
                "key": f"AWW.{r['awips'][3:]}" if r["nnn"] == "AWW" else f"SPS.{r['id']}.{r['seq']}",
                "kind": r["nnn"],
                "product_id": r["id"],
                "office": r["office"],
                "airport": r["awips"][3:] if r["nnn"] == "AWW" else None,
                "headline": headline,
                "body": r["body"],
                "issued": r["issued"].isoformat() if r["issued"] else None,
                "expires": expires.isoformat(),
            },
        })
    return {"type": "FeatureCollection", "features": features}
