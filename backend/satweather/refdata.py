"""`satweather-refdata`: load NWS zone/county shapes and the station list.

UGC codes in products (NMC013, ALZ019, PZZ350) only become places on a
map through these tables. The NWS republishes its shapefiles under
date-coded names (z_16ap26.zip = 16 Apr 2026), so the current ones are
discovered from the weather.gov/gis pages each run and the newest of each
set is taken; a set already loaded from the same file is skipped.

Stations (for METAR/TAF placement) come from aviationweather.gov's
station cache, which is global: EMWIN carries international reports.

Geometry is simplified on load (~200 m): the shapes are drawn on a web
map and unioned per request, and full-resolution coastlines would make
both slow for no visible gain.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import logging
import re
import tempfile
import urllib.request
from datetime import date
from pathlib import Path

from sqlalchemy import text

from satweather.db import get_engine

log = logging.getLogger("satweather.refdata")

UA = "satweather.808.org (GOES-18 HRIT viewer)"
NWS = "https://www.weather.gov"
SIMPLIFY_DEG = 0.002

# kind: (page that links the shapefile, filename prefix)
SETS = {
    "public": ("/gis/PublicZones", "z_"),
    "county": ("/gis/Counties", "c_"),
    "fire": ("/gis/FireZones", "fz"),
    "marine": ("/gis/MarineZones", "mz"),
    "offshore": ("/gis/MarineZones", "oz"),
}
STATIONS_URL = "https://aviationweather.gov/data/cache/stations.cache.json.gz"

MONTHS = {"ja": 1, "fe": 2, "mr": 3, "ap": 4, "my": 5, "jn": 6, "jl": 7, "au": 8, "se": 9, "oc": 10,
          "nv": 11, "de": 12}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def _datecode(stem: str) -> date | None:
    m = re.search(r"(\d{2})([a-z]{2})(\d{2})$", stem)
    if not m or m[2] not in MONTHS:
        return None
    return date(2000 + int(m[3]), MONTHS[m[2]], int(m[1]))


def discover(kind: str) -> str:
    """URL of the newest shapefile zip for a set."""
    page, prefix = SETS[kind]
    html = _get(NWS + page).decode("utf-8", "replace")
    links = set(re.findall(r'(/source/gis/Shapefiles/[A-Za-z]+/' + re.escape(prefix) + r'\w+\.zip)', html))
    dated = [(d, link) for link in links if (d := _datecode(Path(link).stem))]
    if not dated:
        raise RuntimeError(f"no {prefix}*.zip linked from {page}")
    return NWS + max(dated)[1]


def _rows(kind: str, df):
    for r in df.itertuples(index=False):
        if kind in ("public", "fire"):
            ugc, name, state, cwa = f"{r.STATE}Z{r.ZONE}", r.NAME, r.STATE, r.CWA
        elif kind == "county":
            ugc, name, state, cwa = f"{r.STATE}C{str(r.FIPS)[-3:]}", r.COUNTYNAME, r.STATE, r.CWA
        else:  # marine, offshore
            ugc, name, state, cwa = r.ID, r.NAME, None, r.WFO
        if not ugc or r.geometry is None:
            continue
        yield {"ugc": ugc, "name": name or ugc, "state": state, "cwa": cwa, "wkb": r.geometry.wkb}


def load_zones(kind: str, force: bool = False) -> int:
    import pyogrio  # heavy; only this command needs it

    url = discover(kind)
    source = Path(url).stem
    with get_engine().connect() as conn:
        have = conn.execute(text("SELECT source FROM refdata_load WHERE kind = :k"), {"k": kind}).scalar()
    if have == source and not force:
        log.info("%s: %s already loaded", kind, source)
        return 0

    log.info("%s: downloading %s", kind, url)
    with tempfile.TemporaryDirectory() as tmp:
        z = Path(tmp) / f"{source}.zip"
        z.write_bytes(_get(url))
        df = pyogrio.read_dataframe(f"/vsizip/{z}")

    # Several shapes can share a UGC (islands, split counties); merge them.
    rows: dict[str, dict] = {}
    parts: dict[str, list[bytes]] = {}
    for row in _rows(kind, df):
        rows.setdefault(row["ugc"], row)
        parts.setdefault(row["ugc"], []).append(row["wkb"])

    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM ugc_zone WHERE kind = :k"), {"k": kind})
        for ugc, row in rows.items():
            conn.execute(
                text(
                    """
                    WITH g AS (
                        SELECT ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SimplifyPreserveTopology(
                                   ST_Transform(ST_Union(ST_SetSRID(ST_GeomFromWKB(w), 4269)), 4326),
                                   :tol)), 3)) AS geom
                        FROM unnest(CAST(:wkbs AS bytea[])) AS w
                    )
                    INSERT INTO ugc_zone (kind, ugc, name, state, cwa, geom, centroid, source)
                    SELECT :kind, :ugc, :name, :state, :cwa, geom, ST_PointOnSurface(geom), :source
                    FROM g WHERE NOT ST_IsEmpty(geom)
                    """
                ),
                {"kind": kind, "ugc": ugc, "name": row["name"], "state": row["state"], "cwa": row["cwa"],
                 "wkbs": parts[ugc], "tol": SIMPLIFY_DEG, "source": source},
            )
        conn.execute(
            text(
                """
                INSERT INTO refdata_load (kind, source, rows) VALUES (:k, :s, :n)
                ON CONFLICT (kind) DO UPDATE SET source = EXCLUDED.source, rows = EXCLUDED.rows,
                    loaded = now()
                """
            ),
            {"k": kind, "s": source, "n": len(rows)},
        )
    log.info("%s: loaded %d UGCs from %s", kind, len(rows), source)
    return len(rows)


def load_stations() -> int:
    data = json.loads(gzip.GzipFile(fileobj=io.BytesIO(_get(STATIONS_URL))).read())
    rows = []
    for s in data:
        sid = s.get("icaoId") or s.get("id")
        if not sid or s.get("lat") is None or s.get("lon") is None:
            continue
        rows.append({"id": sid, "icao": s.get("icaoId"), "name": s.get("site"), "state": s.get("state") or None,
                     "country": s.get("country"), "elev": s.get("elev"), "lat": s["lat"], "lon": s["lon"]})
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM station"))
        conn.execute(
            text(
                """
                INSERT INTO station (id, icao, name, state, country, elev_m, geom)
                VALUES (:id, :icao, :name, :state, :country, :elev,
                        ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))
                ON CONFLICT (id) DO NOTHING
                """
            ),
            rows,
        )
        conn.execute(
            text(
                """
                INSERT INTO refdata_load (kind, source, rows) VALUES ('station', :s, :n)
                ON CONFLICT (kind) DO UPDATE SET source = EXCLUDED.source, rows = EXCLUDED.rows,
                    loaded = now()
                """
            ),
            {"s": STATIONS_URL, "n": len(rows)},
        )
    log.info("stations: loaded %d", len(rows))
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser(prog="satweather-refdata")
    ap.add_argument("--force", action="store_true", help="reload sets even if the source is unchanged")
    ap.add_argument("--only", choices=[*SETS, "station"], action="append")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    failed = False
    for kind in args.only or [*SETS, "station"]:
        try:
            load_stations() if kind == "station" else load_zones(kind, force=args.force)
        except Exception:
            log.exception("%s failed", kind)
            failed = True
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
