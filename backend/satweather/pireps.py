"""Place PIREPs where they were flown, from their /OV field.

Ingest stores each PIREP at the station that filed it (approx). This
module moves it to its reported position:

    /OV FIM305020         20 NM out on FIM's 305 radial
    /OV 15 SW FAT         15 NM southwest of FAT ("5 NORTH HDF" too)
    /OV JLI               over JLI
    /OV SAN-LAX           along the route: placed midway, still approx
    /OV 3330N 11800W      a latitude/longitude

Radials are magnetic; the declination at the navaid comes from the World
Magnetic Model (pygeomag), within a degree or two of a VOR's published
variation, about a mile at 30 NM. Identifiers are resolved through
Tachbase's lookup_waypoints (FAA NASR airports, navaids and fixes; no
account needed) and cached in the waypoint table, so a lookup leaves the
LAN once per identifier per NASR cycle. If Tachbase cannot be reached
the PIREP keeps its station position and is tried again later; one whose
/OV cannot be read or resolved stays where it is.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime

import httpx
from sqlalchemy import Connection, text

from satweather.config import get_settings

log = logging.getLogger("satweather.pireps")

NM_EARTH = 3440.065  # mean Earth radius in nautical miles
MAX_TRIES = 5
WAYPOINT_DAYS = 28  # a NASR cycle
UNKNOWN_DAYS = 1  # how long a miss is remembered

_OV = re.compile(r"/\s*OV\s*([^/]+)")
_ID = r"[A-Z0-9]{2,5}"
_RADIAL = re.compile(rf"^({_ID}?)(\d{{3}})(\d{{3}})$")
_OFFSET = re.compile(rf"^(\d{{1,3}})\s?(N|NNE|NE|ENE|E|ESE|SE|SSE|S|SSW|SW|WSW|W|WNW|NW|NNW)\s+({_ID})$")
_LATLON = re.compile(r"^(\d{2})(\d{2})([NS])\s*/?\s*(\d{2,3})(\d{2})([EW])$")
_COMPASS = {d: i * 22.5 for i, d in enumerate(
    "N NNE NE ENE E ESE SE SSE S SSW SW WSW W WNW NW NNW".split())}
_WORDS = {"NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW",
          "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W"}
# "HDF VOR": the facility type after the identifier says nothing more.
_TYPE_SUFFIX = re.compile(r"\s+(?:VORTAC|VOR/DME|VOR-DME|VOR|TACAN|NDB|DME|APT|AIRPORT)$")


@dataclass(frozen=True)
class Spec:
    """One end of an /OV: an identifier, optionally offset from it."""
    ident: str | None = None
    bearing: float | None = None  # degrees
    magnetic: bool = False
    dist_nm: float = 0
    lat: float | None = None
    lon: float | None = None


def parse_ov(raw: str) -> tuple[list[Spec], bool] | None:
    """The /OV of a raw PIREP as one point (or two, for a route) and
    whether the result is only approximate. None if there is no /OV or it
    is not a form we read."""
    m = _OV.search(raw)
    if not m:
        return None
    ov = re.sub(r"\s+\d{4}Z?$", "", m[1].strip().upper())  # a time left in the field
    parts = [p.strip() for p in re.split(r"\s*-\s*", ov)] if "-" in ov else [ov]
    if len(parts) > 2:
        return None
    specs = [s for p in parts if (s := _spec(p))]
    if len(specs) != len(parts):
        return None
    return specs, len(specs) == 2


def _spec(s: str) -> Spec | None:
    s = _TYPE_SUFFIX.sub("", s)
    s = re.sub(r"\b(%s)\b" % "|".join(_WORDS), lambda m: _WORDS[m[1]], s)
    if m := _LATLON.match(s):
        lat = int(m[1]) + int(m[2]) / 60
        lon = int(m[4]) + int(m[5]) / 60
        return Spec(lat=-lat if m[3] == "S" else lat, lon=-lon if m[6] == "W" else lon)
    if (m := _RADIAL.match(s)) and m[1]:
        return Spec(ident=m[1], bearing=float(int(m[2]) % 360), magnetic=True, dist_nm=int(m[3]))
    if m := _OFFSET.match(s):
        return Spec(ident=m[3], bearing=_COMPASS[m[2]], dist_nm=int(m[1]))
    if re.fullmatch(_ID, s):
        return Spec(ident=s)
    return None


def destination(lat: float, lon: float, bearing: float, dist_nm: float) -> tuple[float, float]:
    """Great-circle point `dist_nm` from (lat, lon) on a true bearing."""
    if not dist_nm:
        return lat, lon
    d, th = dist_nm / NM_EARTH, math.radians(bearing)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(th))
    l2 = l1 + math.atan2(math.sin(th) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


def midpoint(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    p1, l1, p2, l2 = map(math.radians, (*a, *b))
    bx, by = math.cos(p2) * math.cos(l2 - l1), math.cos(p2) * math.sin(l2 - l1)
    p3 = math.atan2(math.sin(p1) + math.sin(p2), math.hypot(math.cos(p1) + bx, by))
    l3 = l1 + math.atan2(by, math.cos(p1) + bx)
    return math.degrees(p3), (math.degrees(l3) + 540) % 360 - 180


_geomag = None


def declination(lat: float, lon: float, when: datetime) -> float:
    """Magnetic declination in degrees, east positive (WMM 2025)."""
    global _geomag
    if _geomag is None:
        from pygeomag import GeoMag

        _geomag = GeoMag(coefficients_file="wmm/WMM_2025.COF")
    year = when.year + (when.timetuple().tm_yday - 1) / 365.25
    return _geomag.calculate(glat=lat, glon=lon, alt=0, time=year).d


def resolve(conn: Connection, idents: set[str]) -> dict[str, tuple[float, float]]:
    """Positions of identifiers: cached, else from Tachbase (raises on a
    network or HTTP error, so the caller can retry later)."""
    rows = conn.execute(text(
        "SELECT id, lat, lon FROM waypoint WHERE id = ANY(:ids) AND fetched_at > now() - make_interval("
        "days => CASE WHEN lat IS NULL THEN :unk ELSE :ok END)"),
        {"ids": sorted(idents), "ok": WAYPOINT_DAYS, "unk": UNKNOWN_DAYS}).all()
    known = {r[0]: (r[1], r[2]) for r in rows}
    missing = idents - set(known)
    if missing:
        # PIREPs give airports by FAA id (PRB); Tachbase knows them by ICAO.
        ask = sorted(missing | {f"K{i}" for i in missing if len(i) == 3})
        url = get_settings().tachbase_api_url
        r = httpx.get(f"{url}/flights", params={"action": "lookup_waypoints", "ids": ",".join(ask)},
                      headers={"User-Agent": "satweather (PIREP placement)"}, timeout=10)
        r.raise_for_status()
        got = r.json().get("waypoints") or {}
        # Small airports with no ICAO code (F70) are in our station table
        # by FAA id.
        local = {row[0]: {"lat": row[1], "lng": row[2], "type": "airport", "name": row[3]} for row in conn.execute(text(
            "SELECT faa, ST_Y(geom), ST_X(geom), name FROM station WHERE faa = ANY(:ids)"), {"ids": sorted(missing)})}
        for i in sorted(missing):
            w = got.get(i) or got.get(f"K{i}") or local.get(i)
            pos = (w["lat"], w["lng"]) if w and w.get("lat") is not None else (None, None)
            conn.execute(text(
                "INSERT INTO waypoint (id, name, kind, lat, lon) VALUES (:id, :name, :kind, :lat, :lon)"
                " ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, kind = EXCLUDED.kind,"
                " lat = EXCLUDED.lat, lon = EXCLUDED.lon, fetched_at = now()"),
                {"id": i, "name": w and w.get("name"), "kind": w and w.get("type"), "lat": pos[0], "lon": pos[1]})
            known[i] = pos
    return {i: p for i, p in known.items() if p[0] is not None}


def locate(specs: list[Spec], where: dict[str, tuple[float, float]], when: datetime) -> tuple[float, float] | None:
    pts = []
    for s in specs:
        if s.lat is not None:
            pts.append((s.lat, s.lon))
            continue
        if s.ident not in where:
            return None
        lat, lon = where[s.ident]
        if s.bearing is not None:
            true = s.bearing + (declination(lat, lon, when) if s.magnetic else 0)
            lat, lon = destination(lat, lon, true % 360, s.dist_nm)
        pts.append((lat, lon))
    return pts[0] if len(pts) == 1 else midpoint(*pts)


def place_pending(engine, limit: int = 200) -> dict[str, int]:
    """Place PIREPs not yet placed. Returns counts by outcome."""
    counts: dict[str, int] = {}
    if not get_settings().tachbase_api_url:
        return counts
    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT id, raw, coalesce(observed, now()) AS t FROM pirep WHERE placed_at IS NULL"
            " AND place_tries < :max ORDER BY id DESC LIMIT :n"), {"max": MAX_TRIES, "n": limit}).mappings().all()
        parsed = {r["id"]: parse_ov(r["raw"]) for r in rows}
        idents = {s.ident for p in parsed.values() if p for s in p[0] if s.ident}
        try:
            where = resolve(conn, idents) if idents else {}
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("waypoint lookup failed, will retry: %s", exc)
            conn.execute(text("UPDATE pirep SET place_tries = place_tries + 1 WHERE id = ANY(:ids)"),
                         {"ids": [r["id"] for r in rows if parsed[r["id"]]]})
            return {"retry": len(rows)}
        for r in rows:
            p = parsed[r["id"]]
            pos = locate(p[0], where, r["t"]) if p else None
            outcome = "placed" if pos else "unplaced"
            conn.execute(text(
                "UPDATE pirep SET placed_at = now(), approx = CASE WHEN :pos THEN :approx ELSE approx END,"
                " geom = CASE WHEN :pos THEN ST_SetSRID(ST_MakePoint(:lon, :lat), 4326) ELSE geom END"
                " WHERE id = :id"),
                {"id": r["id"], "pos": pos is not None, "approx": bool(p and p[1]),
                 "lat": pos and pos[0], "lon": pos and pos[1]})
            counts[outcome] = counts.get(outcome, 0) + 1
    return counts

