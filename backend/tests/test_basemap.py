import io

import httpx
from pmtiles.reader import MemorySource, Reader
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer

from satweather.basemap import extract


def _archive() -> bytes:
    """A small clustered PMTiles archive: one tile per zoom 0..8 at x=y=0,
    plus a few repeats (as ocean tiles are in the planet)."""
    buf = io.BytesIO()
    w = Writer(buf)
    for z in range(9):
        w.write_tile(zxy_to_tileid(z, 0, 0), f"tile z{z}".encode())
        if z >= 2:
            w.write_tile(zxy_to_tileid(z, 1, 1), b"ocean")
    w.finalize({"tile_type": TileType.MVT, "tile_compression": Compression.GZIP, "min_zoom": 0, "max_zoom": 8,
                "min_lon_e7": -1800000000, "min_lat_e7": -850000000, "max_lon_e7": 1800000000,
                "max_lat_e7": 850000000, "center_zoom": 3, "center_lon_e7": 0, "center_lat_e7": 0},
               {"version": "4.15.2", "name": "test"})
    return buf.getvalue()


def test_extract_low_zooms_by_range_requests(tmp_path):
    blob = _archive()
    ranges = []

    def serve(req: httpx.Request) -> httpx.Response:
        a, b = req.headers["Range"].removeprefix("bytes=").split("-")
        ranges.append((int(a), int(b)))
        return httpx.Response(206, content=blob[int(a):int(b) + 1])

    out = tmp_path / "world.pmtiles"
    with httpx.Client(transport=httpx.MockTransport(serve)) as c:
        n = extract(c, "https://example.test/planet.pmtiles", out, maxzoom=6)
    assert n == 7 + 5  # z0..6 at 0/0, and the repeats from z2
    r = Reader(MemorySource(out.read_bytes()))
    assert (r.header()["min_zoom"], r.header()["max_zoom"]) == (0, 6)
    assert r.get(6, 0, 0) == b"tile z6" and r.get(4, 1, 1) == b"ocean"
    assert r.get(7, 0, 0) is None
    assert r.metadata()["version"] == "4.15.2"
    assert all(b - a < len(blob) for a, b in ranges)  # ranges, not the whole file
