"""The world tier of the basemap (satweather-refdata --only basemap).

swiftcamp's street archive covers CONUS only, and most of GOES-18's disk is
Pacific, Alaska, Hawaii and Mexico. This builds a zoom 0-6 extract of the
Protomaps planet (~45 MB, same schema) into <media_root>/basemap/, which
Apache serves at /media/basemap/; the map draws it under the street layers.

The planet is a clustered PMTiles archive, so every tile up to zoom 6 sits
at the front of its tile data: the directories plus one range request get
them all. The build is the newest one whose schema version matches
swiftcamp's street archive, so one style fits both. A rebuild only happens
when the extract is older than REBUILD_DAYS (borders and coastlines at
these zooms barely change). The previous extract is kept, since open pages
still read it by name until they reload.
"""

from __future__ import annotations

import gzip
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from satweather.config import get_settings

log = logging.getLogger("satweather.basemap")

MAXZOOM = 6
REBUILD_DAYS = 90
SWIFTCAMP = "https://cdn.swiftcamp.app"
PROTOMAPS_BUILDS = "https://build-metadata.protomaps.dev/builds.json"
PROTOMAPS = "https://build.protomaps.com"


def _range(c: httpx.Client, url: str, off: int, n: int) -> bytes:
    r = c.get(url, headers={"Range": f"bytes={off}-{off + n - 1}"})
    r.raise_for_status()
    if len(r.content) != n:
        raise ValueError(f"{url}: asked for {n} bytes at {off}, got {len(r.content)}")
    return r.content


def _header_and_metadata(c: httpx.Client, url: str) -> tuple[dict, dict]:
    from pmtiles.tile import Compression, deserialize_header

    h = deserialize_header(_range(c, url, 0, 127))
    raw = _range(c, url, h["metadata_offset"], h["metadata_length"])
    if h["internal_compression"] == Compression.GZIP:
        raw = gzip.decompress(raw)
    return h, json.loads(raw)


def extract(c: httpx.Client, url: str, out: Path, maxzoom: int = MAXZOOM) -> int:
    """Zoom 0..maxzoom of the PMTiles archive at `url` into `out`. Returns
    the tile count."""
    from pmtiles.tile import deserialize_directory, zxy_to_tileid
    from pmtiles.writer import Writer

    end = zxy_to_tileid(maxzoom + 1, 0, 0)
    h, metadata = _header_and_metadata(c, url)
    tiles: list[tuple[int, int, int]] = []  # tile id, offset, length

    def walk(off: int, n: int) -> None:
        for e in deserialize_directory(_range(c, url, off, n)):
            if e.tile_id >= end:
                break
            if e.run_length == 0:  # a leaf directory
                walk(h["leaf_directory_offset"] + e.offset, e.length)
            else:
                tiles.extend((e.tile_id + k, e.offset, e.length) for k in range(e.run_length) if e.tile_id + k < end)

    walk(h["root_offset"], h["root_length"])
    lo = min(o for _, o, _ in tiles)
    hi = max(o + n for _, o, n in tiles)
    if hi - lo > 500e6:
        raise ValueError(f"zoom 0-{maxzoom} spans {(hi - lo) / 1e6:.0f} MB: not a clustered archive?")
    data = _range(c, url, h["tile_data_offset"] + lo, hi - lo)
    tmp = out.with_name(out.name + ".tmp")
    with open(tmp, "wb") as f:
        w = Writer(f)
        for tid, o, n in sorted(tiles):
            w.write_tile(tid, data[o - lo:o - lo + n])
        header = {k: h[k] for k in ("tile_type", "tile_compression", "min_lon_e7", "min_lat_e7", "max_lon_e7",
                                    "max_lat_e7", "center_lon_e7", "center_lat_e7")}
        header.update(min_zoom=0, max_zoom=maxzoom, center_zoom=min(h["center_zoom"], maxzoom))
        w.finalize(header, metadata)
    tmp.rename(out)
    return len(tiles)


def refresh_world(force: bool = False) -> str:
    """Build the world extract if missing or stale. Returns what it did."""
    d = get_settings().media_root / "basemap"
    d.mkdir(parents=True, exist_ok=True)
    manifest = d / "manifest.json"
    current = json.loads(manifest.read_text()) if manifest.exists() else {}
    if not force and (w := current.get("world")) and (d / w["file"]).exists():
        age = (datetime.now(UTC) - datetime.fromisoformat(w["built"])).days
        if age < REBUILD_DAYS:
            return f"{w['file']} is {age} days old, kept"
    with httpx.Client(timeout=600, follow_redirects=True, headers={"User-Agent": "satweather (basemap)"}) as c:
        street = c.get(f"{SWIFTCAMP}/manifest.json").json()["layers"]["street"]["file"]
        version = _header_and_metadata(c, f"{SWIFTCAMP}/{street}")[1].get("version")
        builds = [b for b in c.get(PROTOMAPS_BUILDS).json() if b.get("version") == version]
        if not builds:
            raise RuntimeError(f"no Protomaps build with schema {version} (swiftcamp's {street})")
        key = max(builds, key=lambda b: b["key"])["key"]  # e.g. 20261023.pmtiles
        name = f"world-z{MAXZOOM}-{key.removesuffix('.pmtiles')}.pmtiles"
        t0 = time.time()
        n = extract(c, f"{PROTOMAPS}/{key}", d / name)
    tmp = manifest.with_name("manifest.json.tmp")
    tmp.write_text(json.dumps({"world": {"file": name, "maxzoom": MAXZOOM, "schema": version, "source": key,
                                         "built": datetime.now(UTC).isoformat()}}))
    tmp.rename(manifest)
    # Keep this extract and the one before it.
    for old in sorted(d.glob("world-z*.pmtiles"), key=lambda p: p.stat().st_mtime)[:-2]:
        old.unlink()
    return f"{name}: {n} tiles, schema {version}, {(d / name).stat().st_size / 1e6:.0f} MB in {time.time() - t0:.0f}s"
