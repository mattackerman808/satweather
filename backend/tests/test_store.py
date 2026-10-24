"""Ingest -> database -> API, against a real PostGIS.

Runs when SATWEATHER_TEST_DATABASE_URL points at a disposable database
(CI provides one as a service container); skipped otherwise. The schema
is rebuilt from the migrations for every run.
"""

import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text

URL = os.environ.get("SATWEATHER_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="SATWEATHER_TEST_DATABASE_URL not set")

FIX = Path(__file__).parent / "fixtures" / "emwin"


@pytest.fixture()
def db(monkeypatch, tmp_path):
    from satweather.config import get_settings
    from satweather.db import get_engine
    from satweather.db.migrate import main as migrate

    monkeypatch.setenv("SATWEATHER_DATABASE_URL", URL)
    monkeypatch.setenv("SATWEATHER_DATA_ROOT", str(tmp_path))
    get_settings.cache_clear()
    get_engine.cache_clear()
    with get_engine().begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    monkeypatch.setattr("sys.argv", ["satweather-migrate"])
    migrate()
    yield get_engine()
    get_engine().dispose()


def _ingest(names, root: Path):
    from satweather import ingest

    emwin = root / "EMWIN"
    emwin.mkdir(exist_ok=True)
    for f in names:
        dst = emwin / f.name
        shutil.copy(f, dst)
        os.utime(dst, (0, datetime(2026, 10, 24, tzinfo=UTC).timestamp()))
    return ingest.scan([emwin], set(), settle_s=0)


def test_ingest_all_fixtures(db, tmp_path):
    counts = _ingest(list(FIX.iterdir()), tmp_path)
    assert counts.get("error", 0) == 0, counts
    with db.connect() as conn:
        missing = conn.execute(
            text(
                "SELECT f.name, f.kind, f.status FROM source_file f"
                " LEFT JOIN text_product p ON p.source_file_id = f.id WHERE p.id IS NULL"
            )
        ).all()
        n = conn.execute(text("SELECT count(*) FROM source_file")).scalar()
        raw = conn.execute(text("SELECT count(*) FROM text_product WHERE parse_error IS NOT NULL")).scalar()
        metars = conn.execute(text("SELECT count(*) FROM metar")).scalar()
    assert n == len(list(FIX.iterdir())), (n, counts)
    assert not missing, missing
    assert raw <= 5 and metars > 0


def test_rescan_is_idempotent(db, tmp_path):
    from satweather import ingest

    _ingest(list(FIX.glob("*-SVR*")), tmp_path)
    with db.connect() as conn:
        seen = ingest.load_seen(conn)
    assert ingest.scan([tmp_path / "EMWIN"], seen, settle_s=0) == {}


def test_search_and_detail(db, tmp_path):
    from fastapi.testclient import TestClient

    from satweather.api.app import app

    _ingest(list(FIX.iterdir()), tmp_path)
    c = TestClient(app)
    hits = c.get("/api/products", params={"q": "quarter size hail"}).json()["items"]
    assert any(h["nnn"] == "SVR" for h in hits)
    svr = next(h for h in hits if h["nnn"] == "SVR")
    d = c.get(f"/api/products/{svr['id']}").json()
    assert d["vtec"][0]["name"] == "Severe Thunderstorm Warning"
    assert d["segments"][0]["polygon"]["type"] == "Polygon"
    assert c.get("/api/products", params={"category": "observation"}).json()["items"]


def test_warning_state_follows_vtec(db, tmp_path):
    """Cancelling every UGC of an event removes it from the active set."""
    _ingest(list(FIX.glob("*-SVR*")), tmp_path)
    with db.begin() as conn:
        conn.execute(text("UPDATE vtec_ugc_state SET ends = now() + interval '1 hour'"))
        conn.execute(text("UPDATE vtec_event_state SET ends = now() + interval '1 hour'"))
    from fastapi.testclient import TestClient

    from satweather.api import warnings
    from satweather.api.app import app

    warnings._cache = None
    fc = TestClient(app).get("/api/warnings/active").json()
    assert [f["properties"]["phenomena"] for f in fc["features"]] == ["SV"]
    assert fc["features"][0]["geometry"]["type"] == "Polygon"
    with db.begin() as conn:
        conn.execute(text("UPDATE vtec_ugc_state SET action = 'CAN'"))
    warnings._cache = None
    assert TestClient(app).get("/api/warnings/active").json()["features"] == []


def test_stations_tafs_and_pireps(db, tmp_path):
    """METAR/TAF/PIREP products -> decoded rows -> station + PIREP APIs."""
    from fastapi.testclient import TestClient

    from satweather.api.app import app

    with db.begin() as conn:
        conn.execute(text(
            "INSERT INTO station (id, icao, faa, name, geom) VALUES"
            " ('KBRL', 'KBRL', 'BRL', 'Burlington', ST_SetSRID(ST_MakePoint(-91.1, 40.8), 4326)),"
            " ('KBFL', 'KBFL', 'BFL', 'Bakersfield', ST_SetSRID(ST_MakePoint(-119.05, 35.43), 4326))"))
    _ingest([*FIX.glob("A_FTUS*TAF*"), *FIX.glob("*-UABA90*"), *FIX.glob("*-SAHOURLY*")], tmp_path)
    with db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM taf")).scalar() > 10
        assert conn.execute(text("SELECT count(*) FROM metar WHERE decoded IS NOT NULL")).scalar() > 0
        bfl = conn.execute(text("SELECT hazards, geom IS NOT NULL FROM pirep WHERE station = 'BFL'")).one()
    assert bfl == (["IC"], True)
    c = TestClient(app)
    s = c.get("/api/stations/KBRL").json()
    assert s["taf"]["decoded"]["station"] == "KBRL"
    assert s["taf"]["decoded"]["periods"]
    d = c.get(f"/api/products/{c.get('/api/products', params={'nnn': 'TAF'}).json()['items'][0]['id']}").json()
    assert {r["kind"] for r in d["reports"]} == {"TAF"} and len(d["reports"]) > 10


def test_redecode_backfills(db, tmp_path):
    from satweather import ingest

    _ingest(list(FIX.glob("*-UABA90*")), tmp_path)
    with db.begin() as conn:
        conn.execute(text("DELETE FROM pirep"))
    assert ingest.redecode()["products"] >= 1
    with db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM pirep")).scalar() == 2


def test_station_declutter_by_zoom(db):
    """Tachbase's tiers: zoomed out only important stations, all from z7."""
    from fastapi.testclient import TestClient

    from satweather.api.app import app

    with db.begin() as conn:
        conn.execute(text(
            "INSERT INTO station (id, icao, name, priority, geom) VALUES"
            " ('KLAX', 'KLAX', 'Los Angeles', 0, ST_SetSRID(ST_MakePoint(-118.4, 33.94), 4326)),"
            " ('K04V', 'K04V', 'Saguache', 5, ST_SetSRID(ST_MakePoint(-106.17, 38.10), 4326)),"
            " ('NZAA', 'NZAA', 'Auckland', 0, ST_SetSRID(ST_MakePoint(174.79, -37.0), 4326))"))
        conn.execute(text("INSERT INTO source_file (name, kind, size, mtime, status) VALUES ('x', 'emwin_text', 1, now(), 'ok')"))
        conn.execute(text(
            "INSERT INTO text_product (source_file_id, wmo, cccc, awips, nnn, office, received, priority, title,"
            " category, raw) SELECT id, 'SAUS70', 'KWBC', 'SAHUS', 'SAH', 'KWBC', now(), 2, 't', 'observation', 'r'"
            " FROM source_file"))
        conn.execute(text(
            "INSERT INTO metar (product_id, station, valid, kind, raw, flight_category)"
            " SELECT p.id, s, now(), 'METAR', s || ' 240000Z', 'VFR' FROM text_product p,"
            " unnest(ARRAY['KLAX','K04V','NZAA']) s"))
    c = TestClient(app)

    def ids(**q):
        return sorted(f["properties"]["station"] for f in c.get("/api/stations/latest", params=q).json()["features"])

    usa = "-130,20,-60,55"
    assert ids(bbox=usa, zoom=3) == ["KLAX"]
    assert ids(bbox=usa, zoom=8) == ["K04V", "KLAX"]
    # Across the antimeridian: 160°E .. 110°W (KLAX is at 118.4°W).
    assert ids(bbox="160,-50,-110,60", zoom=3) == ["KLAX", "NZAA"]


def _png(path: Path, w: int = 64, h: int = 48) -> None:
    """A real grayscale PNG, written without an image library."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(b"\x00" + bytes((x * 4) % 256 for x in range(w)) for _ in range(h))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def test_images_catalog_convert_and_list(db, tmp_path, monkeypatch):
    """IMAGES/ -> image_frame (pending) -> WebP copies (ok) -> loop API."""
    from fastapi.testclient import TestClient

    from satweather.api.app import app
    from satweather.config import get_settings
    from satweather.imagery import Scanner

    media = tmp_path / "media"
    monkeypatch.setenv("SATWEATHER_MEDIA_ROOT", str(media))
    get_settings.cache_clear()
    from datetime import timedelta

    scan = tmp_path / "IMAGES" / "GOES-18" / "Mesoscale 1"
    now = datetime.now(UTC).replace(microsecond=0)
    times = [now - timedelta(minutes=11), now - timedelta(minutes=10)]
    for t in times:
        _png(scan / f"{t:%Y-%m-%d_%H-%M-%S}" / f"G18_13_{t:%Y%m%dT%H%M%S}Z.png")
        _png(scan / f"{t:%Y-%m-%d_%H-%M-%S}" / "abi_rgb_ABI_False_Color_map.png")
    last = scan / f"{times[1]:%Y-%m-%d_%H-%M-%S}"
    shutil.copy(Path(__file__).parent / "fixtures" / "images" / "himawari_product.cbor", last / "product.cbor")
    (last / f"G18_2_{times[1]:%Y%m%dT%H%M%S}Z.png").touch()  # still being written

    s = Scanner(tmp_path, set())
    assert s.scan(db.begin, settle_s=0) == 4
    assert s.scan(db.begin, settle_s=0) == 0  # seen, and the empty file is still unsettled
    with db.connect() as conn:
        rows = conn.execute(text("SELECT product, overlay, status, projection->>'type' FROM image_frame"
                                 " ORDER BY scan_time, product")).all()
    assert [tuple(r) for r in rows] == [("13", False, "pending", None), ("ABI_False_Color", True, "pending", None),
                                        ("13", False, "pending", "geos"), ("ABI_False_Color", True, "pending", "geos")]

    try:  # libvips is a system library; without it pyvips raises OSError, not ImportError
        import pyvips  # noqa: F401
    except Exception as exc:
        pytest.skip(f"pyvips unavailable: {exc}")
    from satweather import imaging

    while imaging.process_one():
        pass
    c = TestClient(app)
    cat = c.get("/api/imagery/catalog").json()
    assert [(g["satellite"], g["sector"]) for g in cat["sources"]] == [("GOES-18", "Mesoscale 1")]
    assert [(p["product"], p["overlay"], p["plain"]) for p in cat["sources"][0]["products"]] == \
        [("ABI_False_Color", True, False), ("13", False, True)]
    f = c.get("/api/imagery/frames", params={"satellite": "GOES-18", "sector": "Mesoscale 1",
                                             "product": "13"}).json()["frames"]
    assert [datetime.fromisoformat(x["scan_time"]) for x in f] == times
    assert f[0]["display"].startswith("/media/sat/goes-18/mesoscale-1/") and (f[0]["width"], f[0]["height"]) == (64, 48)
    assert (media / f[0]["display"].removeprefix("/media/")).stat().st_size > 0
