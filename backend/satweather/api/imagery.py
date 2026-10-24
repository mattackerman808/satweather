"""Satellite imagery: what is available, and the frames of one loop.

Paths in responses are under /media/ (Apache serves media_root there).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from satweather.db import get_engine
from satweather.imagery import SATELLITES, label

router = APIRouter(prefix="/api")

SECTORS = ("Full Disk", "Mesoscale 1", "Mesoscale 2")


def _media(rel: str | None) -> str | None:
    return f"/media/{rel}" if rel else None


@router.get("/imagery/catalog")
def catalog(days: int = Query(30, ge=1, le=90)) -> dict:
    """Every satellite/sector with its products: composites first, then
    bands in order. `overlay`/`plain` say which variants exist (bands
    have no borders-drawn variant)."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT satellite, sector, product, kind, bool_or(overlay) AS overlay, bool_or(NOT overlay) AS plain,
                       count(*) AS n, max(scan_time) AS last, min(scan_time) AS first
                FROM image_frame WHERE status = 'ok' AND scan_time > now() - make_interval(days => :d)
                GROUP BY satellite, sector, product, kind
                """
            ),
            {"d": days},
        ).mappings().all()
        queue = dict(conn.execute(text(
            "SELECT status, count(*) FROM image_frame WHERE status IN ('pending', 'error') GROUP BY status")).all())
    groups: dict[tuple[str, str], dict] = {}
    for r in rows:
        g = groups.setdefault((r["satellite"], r["sector"]),
                              {"satellite": r["satellite"], "sector": r["sector"], "last": None, "products": []})
        g["last"] = max(filter(None, [g["last"], r["last"]]))
        g["products"].append({"product": r["product"], "kind": r["kind"],
                              "label": label(r["satellite"], r["product"], r["kind"]),
                              "overlay": r["overlay"], "plain": r["plain"], "n": r["n"],
                              "first": r["first"], "last": r["last"]})
    for g in groups.values():
        g["products"].sort(key=lambda p: (p["kind"] != "rgb", int(p["product"]) if p["kind"] == "band" else 0,
                                          p["label"]))

    def order(g: dict):
        sat = SATELLITES.index(g["satellite"]) if g["satellite"] in SATELLITES else 99
        sec = SECTORS.index(g["sector"]) if g["sector"] in SECTORS else 99
        return sat, sec

    return {"sources": sorted(groups.values(), key=order),
            "queue": {"pending": queue.get("pending", 0), "error": queue.get("error", 0)}}


@router.get("/imagery/frames")
def frames(
    satellite: str,
    sector: str,
    product: str,
    overlay: bool = False,
    hours: float = Query(6, gt=0, le=24 * 30),
    limit: int = Query(300, ge=1, le=2000),
) -> dict:
    """Frames of one loop, oldest first, the newest `limit` of them."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT * FROM (
                    SELECT id, scan_time, display, thumb, width, height FROM image_frame
                    WHERE status = 'ok' AND satellite = :sat AND sector = :sector AND product = :product
                      AND overlay = :overlay AND scan_time > now() - make_interval(secs => :secs)
                    ORDER BY scan_time DESC LIMIT :limit
                ) f ORDER BY scan_time
                """
            ),
            {"sat": satellite, "sector": sector, "product": product, "overlay": overlay,
             "secs": hours * 3600, "limit": limit},
        ).mappings().all()
    if not rows and satellite not in SATELLITES:
        raise HTTPException(404, f"unknown satellite {satellite}")
    return {"frames": [{"id": r["id"], "scan_time": r["scan_time"], "display": _media(r["display"]),
                        "thumb": _media(r["thumb"]), "width": r["width"], "height": r["height"]} for r in rows]}
