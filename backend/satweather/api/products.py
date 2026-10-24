"""Text products: list/search, detail, and per-type counts."""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from satweather.catalog import CATEGORIES, describe
from satweather.config import get_settings
from satweather.emwin import archived_path
from satweather.db import get_engine

router = APIRouter(prefix="/api")


@router.get("/categories")
def categories() -> dict:
    return CATEGORIES


@router.get("/products/types")
def product_types(hours: int = Query(24, ge=1, le=24 * 30)) -> list[dict]:
    """Each product type seen recently, with a count, for the filter list."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT nnn, category, count(*) AS n, max(received) AS last
                FROM text_product WHERE received > now() - make_interval(hours => :h)
                GROUP BY nnn, category ORDER BY n DESC
                """
            ),
            {"h": hours},
        ).mappings()
        return [{**r, "name": describe(r["nnn"])[0]} for r in rows]


@router.get("/products")
def list_products(
    q: str | None = None,
    nnn: list[str] = Query(default=[]),
    category: list[str] = Query(default=[]),
    office: str | None = None,
    awips: str | None = None,
    ugc: str | None = None,
    before: datetime | None = None,
    before_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    """Newest first. Page with before/before_id taken from the last row."""
    where, params = [], {"limit": limit}
    if q:
        where.append("tsv @@ websearch_to_tsquery('english', :q)")
        params["q"] = q
    if nnn:
        where.append("nnn = ANY(:nnn)")
        params["nnn"] = [n.upper() for n in nnn]
    if category:
        where.append("category = ANY(:cat)")
        params["cat"] = category
    if office:
        where.append("office = :office")
        params["office"] = office.upper()
    if awips:
        where.append("awips = :awips")
        params["awips"] = awips.upper()
    if ugc:
        where.append("EXISTS (SELECT 1 FROM product_segment s WHERE s.product_id = p.id AND :ugc = ANY(s.ugcs))")
        params["ugc"] = ugc.upper()
    if before is not None:
        where.append("(received, id) < (:before, :before_id)")
        params["before"] = before
        params["before_id"] = before_id if before_id is not None else 2**62
    snippet = (
        "ts_headline('english', raw, websearch_to_tsquery('english', :q), "
        "'MaxFragments=2, MaxWords=18, MinWords=6, StartSel=<<, StopSel=>>')"
        if q else "NULL"
    )
    sql = f"""
        SELECT id, received, issued, awips, nnn, office, title, category, priority,
               parse_error IS NOT NULL AS raw_only, {snippet} AS snippet,
               EXISTS (SELECT 1 FROM vtec v WHERE v.product_id = p.id) AS has_vtec
        FROM text_product p
        {"WHERE " + " AND ".join(where) if where else ""}
        ORDER BY received DESC, id DESC
        LIMIT :limit
    """
    with get_engine().connect() as conn:
        items = [dict(r) for r in conn.execute(text(sql), params).mappings()]
    nxt = None
    if len(items) == limit:
        nxt = {"before": items[-1]["received"].isoformat(), "before_id": items[-1]["id"]}
    return {"items": items, "next": nxt}


@router.get("/products/{pid}")
def get_product(pid: int) -> dict:
    with get_engine().connect() as conn:
        p = conn.execute(
            text(
                """
                SELECT p.id, p.received, p.issued, p.wmo, p.cccc, p.awips, p.nnn, p.office, p.title,
                       p.category, p.priority, p.raw, p.parse_error, f.name AS filename, f.mtime
                FROM text_product p JOIN source_file f ON f.id = p.source_file_id
                WHERE p.id = :id
                """
            ),
            {"id": pid},
        ).mappings().first()
        if p is None:
            raise HTTPException(404, "no such product")
        segs = conn.execute(
            text(
                """
                SELECT s.id, s.seq, s.ugcs, s.ugc_expire, s.headlines, s.motion, s.tags,
                       ST_AsGeoJSON(s.polygon, 4) AS polygon,
                       COALESCE((SELECT json_agg(json_build_object(
                           'ugc', z.ugc, 'name', z.name, 'kind', z.kind) ORDER BY z.ugc)
                           FROM (SELECT DISTINCT ON (ugc) ugc, name, kind FROM ugc_zone
                                 WHERE ugc = ANY(s.ugcs) ORDER BY ugc, kind) z), '[]') AS places
                FROM product_segment s WHERE s.product_id = :id ORDER BY s.seq
                """
            ),
            {"id": pid},
        ).mappings().all()
        vtec = conn.execute(
            text(
                """
                SELECT segment_id, action, office, phenomena, significance, etn, year, begins, ends
                FROM vtec WHERE product_id = :id ORDER BY id
                """
            ),
            {"id": pid},
        ).mappings().all()
        metars = conn.execute(
            text("SELECT count(*) FROM metar WHERE product_id = :id"), {"id": pid}
        ).scalar()
        # Decoded reports inside a collective, for METAR/TAF/PIREP products.
        reports = [dict(r) for r in conn.execute(
            text(
                """
                SELECT 'METAR' AS kind, station, valid AS time, raw, flight_category, decoded
                FROM metar WHERE product_id = :id
                UNION ALL
                SELECT 'TAF', station, issued, raw,
                       decoded->'periods'->0->'summary'->>'flight_category', decoded
                FROM taf WHERE product_id = :id
                UNION ALL
                SELECT 'PIREP', station, observed, raw, NULL, decoded
                FROM pirep WHERE product_id = :id
                ORDER BY station
                LIMIT 300
                """
            ),
            {"id": pid},
        ).mappings()]

    from satweather.api.warnings import vtec_label

    out = dict(p)
    # satweather-housekeep moves files out of EMWIN/ after a few hours.
    live = f"EMWIN/{p['filename']}"
    out["orig_path"] = live if (get_settings().data_root / live).exists() else str(
        archived_path(p["filename"], p["mtime"].timestamp()))
    del out["mtime"]
    out["type_name"] = describe(p["nnn"])[0]
    out["segments"] = [
        {**dict(s), "polygon": json.loads(s["polygon"]) if s["polygon"] else None} for s in segs
    ]
    out["vtec"] = [{**dict(v), **vtec_label(v["phenomena"], v["significance"])} for v in vtec]
    out["metar_count"] = metars
    out["reports"] = reports
    return out
