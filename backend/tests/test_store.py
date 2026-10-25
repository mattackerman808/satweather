"""Ingest -> database -> API, against a real PostGIS.

Runs when SATWEATHER_TEST_DATABASE_URL points at a disposable database
(CI provides one as a service container); skipped otherwise. The schema
is rebuilt from the migrations for every run.
"""

import os
import shutil
import time
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


def _png(path: Path, w: int = 64, h: int = 48, seed: int = 0) -> None:
    """A real grayscale PNG, written without an image library. Different
    seeds give different bytes (identical originals are dups)."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(b"\x00" + bytes((x * 4 + seed) % 256 for x in range(w)) for _ in range(h))
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
    for i, t in enumerate(times):
        _png(scan / f"{t:%Y-%m-%d_%H-%M-%S}" / f"G18_13_{t:%Y%m%dT%H%M%S}Z.png", seed=i)
        _png(scan / f"{t:%Y-%m-%d_%H-%M-%S}" / "abi_rgb_ABI_False_Color_map.png", seed=i)
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
    assert [(g["source"], g["sector"], g["group"]) for g in cat["sources"]] == [("GOES-18", "Mesoscale 1", "satellite")]
    assert [(p["product"], p["overlay"], p["plain"]) for p in cat["sources"][0]["products"]] == \
        [("ABI_False_Color", True, False), ("13", False, True)]
    f = c.get("/api/imagery/frames", params={"source": "GOES-18", "sector": "Mesoscale 1",
                                             "product": "13"}).json()["frames"]
    assert [datetime.fromisoformat(x["scan_time"]) for x in f] == times
    assert f[0]["display"].startswith("/media/sat/goes-18/mesoscale-1/") and (f[0]["width"], f[0]["height"]) == (64, 48)
    assert f[0]["orig"].startswith("/orig/IMAGES/GOES-18/Mesoscale%201/")
    assert (media / f[0]["display"].removeprefix("/media/")).stat().st_size > 0


def test_emwin_graphics_archive_and_dups(db, tmp_path, monkeypatch):
    """EMWIN graphic -> image_frame at ingest; converted after rtl archived
    it; a byte-identical re-send is a dup with no copies."""
    from satweather import ingest
    from satweather.config import get_settings
    from satweather.emwin import archived_path

    monkeypatch.setenv("SATWEATHER_MEDIA_ROOT", str(tmp_path / "media"))
    get_settings.cache_clear()
    emwin = tmp_path / "EMWIN"
    names = ["A_PNUS71KWBC240322_C_KWIN_20261024032200_000001-2-RADPACSW.GIF",
             "A_PNUS71KWBC240422_C_KWIN_20261024042200_000002-2-RADPACSW.GIF"]
    for n in names:
        _png(emwin / n)  # libvips goes by content, not extension
    assert ingest.scan([emwin], set(), settle_s=0) == {"image": 2}
    with db.connect() as conn:
        rows = conn.execute(text("SELECT path, source, sector, product FROM image_frame ORDER BY path")).all()
    assert [tuple(r) for r in rows] == [(f"EMWIN/{n}", "NWS", "Radar", "RADPACSW") for n in names]

    first = emwin / names[0]
    moved = tmp_path / archived_path(names[0], first.stat().st_mtime)
    moved.parent.mkdir(parents=True)
    first.rename(moved)
    try:
        import pyvips  # noqa: F401
    except Exception as exc:
        pytest.skip(f"pyvips unavailable: {exc}")
    from satweather import imaging

    while imaging.process_one():
        pass
    with db.connect() as conn:
        st = dict(conn.execute(text("SELECT path, status FROM image_frame")).all())
    # Newest first: the 04:22 copy is made, the archived 03:22 one is its dup.
    assert st == {f"EMWIN/{names[1]}": "ok", f"EMWIN/{names[0]}": "dup"}


def test_housekeeping(db, tmp_path):
    """Archive only old, ingested EMWIN files; delete only old originals
    whose names parse; drop old copies with their rows."""
    from datetime import timedelta

    from satweather import housekeeping as hk
    from satweather.emwin import archived_path

    emwin = tmp_path / "EMWIN"
    emwin.mkdir()
    old = time.time() - 5 * 3600
    names = {"ingested": "A_PNUS71KWBC240322_C_KWIN_20261024032200_000001-2-RADPACSW.GIF",
             "unseen": "A_PNUS71KWBC240323_C_KWIN_20261024032300_000002-2-RADPACNW.GIF",
             "fresh": "A_PNUS71KWBC240324_C_KWIN_20261024032400_000003-2-RADREFUS.GIF"}
    for k, n in names.items():
        (emwin / n).write_bytes(b"x")
        if k != "fresh":
            os.utime(emwin / n, (old, old))
    with db.begin() as conn:
        for k in ("ingested", "fresh"):
            conn.execute(text("INSERT INTO source_file (name, kind, size, mtime, status)"
                              " VALUES (:n, 'emwin_image', 1, now(), 'ok')"), {"n": names[k]})
    assert hk.archive_emwin(tmp_path, 3, dry=True) == 1 and (emwin / names["ingested"]).exists()
    assert hk.archive_emwin(tmp_path, 3) == 1
    assert (tmp_path / archived_path(names["ingested"], old)).exists()
    assert sorted(p.name for p in emwin.iterdir() if p.is_file()) == sorted([names["unseen"], names["fresh"]])

    now = datetime.now(UTC)
    stamp = lambda t: f"{t:%Y-%m-%d_%H-%M-%S}"  # noqa: E731
    fd = tmp_path / "IMAGES" / "GOES-18" / "Full Disk"
    for t in (now - timedelta(days=20), now - timedelta(days=1)):
        (fd / stamp(t)).mkdir(parents=True)
        (fd / stamp(t) / "x.png").write_bytes(b"x")
    (fd / "keep-me").mkdir()
    (tmp_path / "L2" / "GOES-18" / "Full Disk" / stamp(now - timedelta(days=20))).mkdir(parents=True)
    (tmp_path / "IMAGES" / "Himawari" / stamp(now - timedelta(days=20))).mkdir(parents=True)
    nws = tmp_path / "IMAGES" / "NWS"
    nws.mkdir()
    for t in (now - timedelta(days=20), now):
        (nws / f"{t:%Y%m%d%H%M%S}-pac24_latestBW.lrit.gif").write_bytes(b"x")
    (nws / "notes.txt").write_bytes(b"x")
    arch = emwin / "archive"
    (arch / f"{now - timedelta(days=20):%Y-%m-%d}").mkdir()
    (arch / "misc").mkdir()

    assert hk.prune_originals(tmp_path, 0) == {"scans": 0, "charts": 0, "emwin_days": 0}  # keep forever
    assert hk.prune_originals(tmp_path, 14, dry=True) == {"scans": 3, "charts": 1, "emwin_days": 1}
    assert hk.prune_originals(tmp_path, 14) == {"scans": 3, "charts": 1, "emwin_days": 1}
    assert sorted(p.name for p in fd.iterdir()) == sorted([stamp(now - timedelta(days=1)), "keep-me"])
    assert sorted(p.name for p in nws.iterdir()) == sorted([f"{now:%Y%m%d%H%M%S}-pac24_latestBW.lrit.gif",
                                                            "notes.txt"])
    assert sorted(p.name for p in arch.iterdir()) == sorted(["misc", archived_path(names["ingested"], old).parent.name])

    media = tmp_path / "media"
    for i, age in enumerate((40, 1)):
        rel = f"sat/goes-18/full-disk/{i}/x"
        (media / rel).parent.mkdir(parents=True)
        for suffix in (".webp", "_t.webp"):
            (media / f"{rel}{suffix}").write_bytes(b"x")
        with db.begin() as conn:
            conn.execute(text(
                "INSERT INTO image_frame (path, source, sector, product, kind, scan_time, size, mtime, status,"
                " display, thumb) VALUES (:p, 'GOES-18', 'Full Disk', '13', 'band', now() - make_interval(days => :a),"
                " 1, now(), 'ok', :d, :t)"), {"p": f"p{i}", "a": age, "d": f"{rel}.webp", "t": f"{rel}_t.webp"})
    assert hk.prune_copies(media, 30, dry=True) == 1
    assert hk.prune_copies(media, 30) == 1
    assert not (media / "sat/goes-18/full-disk/0").exists() and (media / "sat/goes-18/full-disk/1/x.webp").exists()
    with db.connect() as conn:
        assert conn.execute(text("SELECT path FROM image_frame")).scalars().all() == ["p1"]


def test_zone_at_point(db):
    """Forecast, hazards and products for a clicked point."""
    from fastapi.testclient import TestClient

    from satweather.api.app import app

    sq = "ST_Multi(ST_GeomFromText('POLYGON((-123 37,-122 37,-122 38,-123 38,-123 37))', 4326))"
    with db.begin() as conn:
        for kind, ugc in (("public", "CAZ508"), ("county", "CAC001"), ("fire", "CAZ508")):
            conn.execute(text(f"INSERT INTO ugc_zone (kind, ugc, name, state, cwa, geom, centroid, source) VALUES"
                              f" (:k, :u, 'SF Bay Shoreline', 'CA', 'MTR', {sq}, ST_Centroid({sq}), 't')"),
                         {"k": kind, "u": ugc})
        ids = {}
        for i, (nnn, title) in enumerate((("ZFP", "Zone Forecast"), ("ZFP", "Zone Forecast"), ("PFM", "Point Forecast"),
                                          ("WSW", "Winter Storm"), ("SVR", "Severe Thunderstorm Warning"))):
            conn.execute(text("INSERT INTO source_file (name, kind, size, mtime, status) VALUES (:n, 'emwin_text', 1, now(), 'ok')"),
                         {"n": f"f{i}"})
            ids[i] = conn.execute(text(
                "INSERT INTO text_product (source_file_id, wmo, cccc, awips, nnn, office, issued, received, priority, title,"
                " category, raw) SELECT id, 'FPUS56', 'KMTR', :nnn || 'MTR', :nnn, 'MTR', now() - make_interval(mins => :age),"
                " now() - make_interval(mins => :age), 3, :title, 'forecast', 'r' FROM source_file WHERE name = :n RETURNING id"),
                {"nnn": nnn, "title": title, "age": 60 - i, "n": f"f{i}"}).scalar()
            ugcs = ["CAC001"] if nnn == "SVR" else ["CAZ508", "CAZ509"]
            poly = "POLYGON((-122.2 37.1,-122.1 37.1,-122.1 37.2,-122.2 37.1))" if nnn == "SVR" else None
            conn.execute(text("INSERT INTO product_segment (product_id, seq, ugcs, headlines, body, polygon)"
                              " VALUES (:p, 1, :u, :h, :b, ST_GeomFromText(:poly, 4326))"),
                         {"p": ids[i], "u": ugcs, "h": [f"H{i}"], "b": f"body {i}", "poly": poly})
        for pid, ph, sig, ugc, poly in ((ids[3], "WS", "W", "CAZ508", None),
                                        (ids[4], "SV", "W", "CAC001", "POLYGON((-122.2 37.1,-122.1 37.1,-122.1 37.2,-122.2 37.1))")):
            conn.execute(text(
                "INSERT INTO vtec_event_state (office, phenomena, significance, etn, year, action, polygon, product_id, issued)"
                " VALUES ('KMTR', :ph, :sig, 1, 2026, 'NEW', ST_GeomFromText(:poly, 4326), :p, now())"),
                {"ph": ph, "sig": sig, "poly": poly, "p": pid})
            conn.execute(text(
                "INSERT INTO vtec_ugc_state (office, phenomena, significance, etn, year, ugc, action, ends, product_id, issued)"
                " VALUES ('KMTR', :ph, :sig, 1, 2026, :u, 'NEW', now() + interval '6 hours', :p, now())"),
                {"ph": ph, "sig": sig, "u": ugc, "p": pid})
    c = TestClient(app)
    d = c.get("/api/zones/at", params={"lon": -122.5, "lat": 37.5}).json()
    assert d["zone"]["ugc"] == "CAZ508" and d["zone"]["kind"] == "public"
    assert d["forecast"]["product_id"] == ids[1] and d["forecast"]["headlines"] == ["H1"]  # the newer ZFP
    assert [h["phenomena"] for h in d["hazards"]] == ["WS"]  # the SVR polygon is elsewhere in the county
    assert [p["nnn"] for p in d["products"]] == ["PFM"]  # not the ZFP again, not the hazards' products
    inside = c.get("/api/zones/at", params={"lon": -122.12, "lat": 37.12}).json()
    assert sorted(h["phenomena"] for h in inside["hazards"]) == ["SV", "WS"]
    sea = c.get("/api/zones/at", params={"lon": -150, "lat": 20}).json()
    assert sea["zone"] is None and sea["forecast"] is None



def test_offices(db):
    """Office areas (union of public zones) named from their newest AFD."""
    from fastapi.testclient import TestClient

    from satweather.api import offices
    from satweather.api.app import app

    def sq(x0, x1):
        return f"ST_Multi(ST_GeomFromText('POLYGON(({x0} 37,{x1} 37,{x1} 38,{x0} 38,{x0} 37))', 4326))"

    with db.begin() as conn:
        for ugc, cwa, g in (("CAZ508", "MTR", sq(-123, -122)), ("CAZ509", "MTR", sq(-122, -121)),
                            ("ORZ021", "MFR", sq(-124, -123))):
            conn.execute(text(f"INSERT INTO ugc_zone (kind, ugc, name, state, cwa, geom, centroid, source)"
                              f" VALUES ('public', :u, 'z', 'CA', :c, {g}, ST_Centroid({g}), 't')"), {"u": ugc, "c": cwa})
        conn.execute(text(f"INSERT INTO ugc_zone (kind, ugc, name, state, cwa, geom, centroid, source)"
                          f" VALUES ('county', 'CAC001', 'c', 'CA', 'STO', {sq(-121, -120)}, ST_Centroid({sq(-121, -120)}), 't')"))
        ids = []
        for i, age in enumerate((120, 30)):
            conn.execute(text("INSERT INTO source_file (name, kind, size, mtime, status) VALUES (:n, 'emwin_text', 1, now(), 'ok')"),
                         {"n": f"afd{i}"})
            ids.append(conn.execute(text(
                "INSERT INTO text_product (source_file_id, wmo, cccc, awips, nnn, office, issued, received, priority, title,"
                " category, raw) SELECT id, 'FXUS66', 'KMTR', 'AFDMTR', 'AFD', 'KMTR', now() - make_interval(mins => :age),"
                " now() - make_interval(mins => :age), 2, 'Area Forecast Discussion', 'discussion',"
                " :raw FROM source_file WHERE name = :n RETURNING id"),
                {"age": age, "n": f"afd{i}",
                 "raw": f"FXUS66 KMTR 250708\nAFDMTR\n\nArea Forecast Discussion\n"
                        f"National Weather Service {'Monterey' if i == 0 else 'San Francisco'} CA\n"}).scalar())
    offices._cache = None
    c = TestClient(app)
    fc = c.get("/api/offices").json()
    by = {f["properties"]["office"]: f for f in fc["features"]}
    assert sorted(by) == ["MFR", "MTR"]  # public zones only: STO has just a county
    assert by["MTR"]["properties"]["name"] == "San Francisco CA"  # the newer AFD
    assert by["MFR"]["properties"]["name"] is None
    assert by["MTR"]["geometry"]["type"] == "MultiPolygon"
    items = c.get("/api/offices/afd").json()["items"]
    assert [(i["office"], i["product_id"]) for i in items] == [("MTR", ids[1])]


def test_statements_active(db):
    """SPS polygons in effect, and the newest AWW per airport unless cancelled."""
    from fastapi.testclient import TestClient

    from satweather.api.app import app

    box = "POLYGON((-104 32,-103.9 32,-103.9 32.1,-104 32,-104 32))"
    rows = [
        # name, nnn, awips, age (min), segments [(ugcs, expire in min, headline, body, polygon)]
        ("s1", "SPS", "SPSMAF", 10, [(["NMZ027"], 20, "A STRONG THUNDERSTORM WILL IMPACT EDDY COUNTY", "storm", box)]),
        ("s2", "SPS", "SPSMAF", 90, [(["NMZ028"], -30, "OLD STORM", "old", box)]),
        ("s3", "SPS", "SPSLWX", 5, [(["MDZ001"], 60, "PATCHY FOG", "fog", None)]),
        ("a1", "AWW", "AWWBZN", 60, [(["MTZ326"], 60, "AIRPORT WEATHER WARNING", "Fog with visibility of 1/2 mile or less.", box),
                                      ([], None, "", "", None)]),
        ("a2", "AWW", "AWWBZN", 5, [(["MTZ326"], 60, "AIRPORT WEATHER WARNING", "The Airport Weather Warning has been cancelled.", box)]),
        ("a3", "AWW", "AWWIWA", 15, [(["AZZ544"], 45, "AIRPORT WEATHER WARNING", "Thunderstorms within 5 miles.", box)]),
    ]
    ids = {}
    with db.begin() as conn:
        for name, nnn, awips, age, segs in rows:
            conn.execute(text("INSERT INTO source_file (name, kind, size, mtime, status) VALUES (:n, 'emwin_text', 1, now(), 'ok')"),
                         {"n": name})
            ids[name] = conn.execute(text(
                "INSERT INTO text_product (source_file_id, wmo, cccc, awips, nnn, office, issued, received, priority, title,"
                " category, raw) SELECT id, 'WWUS84', 'KMAF', :awips, :nnn, 'KMAF', now() - make_interval(mins => :age),"
                " now() - make_interval(mins => :age), 3, 't', 'statement', 'r' FROM source_file WHERE name = :n RETURNING id"),
                {"awips": awips, "nnn": nnn, "age": age, "n": name}).scalar()
            for seq, (ugcs, exp, headline, body, poly) in enumerate(segs, 1):
                conn.execute(text(
                    "INSERT INTO product_segment (product_id, seq, ugcs, ugc_expire, headlines, body, polygon) VALUES"
                    " (:p, :seq, :u, CASE WHEN CAST(:exp AS int) IS NULL THEN NULL ELSE now() + make_interval(mins => :exp) END,"
                    " :h, :b, ST_GeomFromText(:poly, 4326))"),
                    {"p": ids[name], "seq": seq, "u": ugcs, "exp": exp, "h": [headline] if headline else [], "b": body, "poly": poly})
    fc = TestClient(app).get("/api/statements/active").json()
    got = {f["properties"]["key"]: f["properties"] for f in fc["features"]}
    # s2 expired, s3 has no polygon, BZN's newest AWW cancels the older one.
    assert sorted(got) == sorted([f"SPS.{ids['s1']}.1", "AWW.IWA"])
    assert got[f"SPS.{ids['s1']}.1"]["headline"] == "A STRONG THUNDERSTORM WILL IMPACT EDDY COUNTY"
    assert got["AWW.IWA"]["airport"] == "IWA" and got["AWW.IWA"]["kind"] == "AWW"
    assert got["AWW.IWA"]["body"] == "Thunderstorms within 5 miles."

def _mcd(t, number: int, hours: int) -> str:
    """An SPC mesoscale discussion as EMWIN carries it, issued at t and
    valid for `hours`."""
    from datetime import timedelta

    end = t + timedelta(hours=hours)
    local = (t - timedelta(hours=5)).strftime("%I%M %p CDT %a %b %d %Y").lstrip("0")
    return (f"ACUS11 KWNS {t:%d%H%M}\nSWOMCD\nSPC MCD {t:%d%H%M}\nTXZ000-OKZ000-{end:%d%H%M}-\n\n"
            f"Mesoscale Discussion {number}\nNWS Storm Prediction Center Norman OK\n{local}\n\n"
            f"Areas affected...Parts of north Texas into southern Oklahoma\n\n"
            f"Concerning...Severe potential...Watch likely\n\n"
            f"Valid {t:%d%H%M}Z - {end:%d%H%M}Z\n\n"
            f"Probability of Watch Issuance...80 percent\n\n"
            f"SUMMARY...Supercells capable of large hail are expected.\n\n"
            f"..Forecaster.. {t:%m/%d/%Y}\n\nATTN...WFO...FWD...OUN...\n\n"
            f"LAT...LON   33449829 34029723 33679622 33029674 32729770 33449829\n\n")


def test_discussions_active(db, tmp_path):
    from datetime import timedelta

    from fastapi.testclient import TestClient

    from satweather import ingest
    from satweather.api.app import app

    now = datetime.now(UTC).replace(second=0, microsecond=0)
    emwin = tmp_path / "EMWIN"
    emwin.mkdir()
    for number, issued, hours in ((1234, now - timedelta(minutes=10), 2), (1201, now - timedelta(hours=5), 2)):
        name = f"A_ACUS11KWNS{issued:%d%H%M}_C_KWIN_{issued:%Y%m%d%H%M%S}_00{number}-2-SWOMCDUS.TXT"
        (emwin / name).write_text(_mcd(issued, number, hours))
    assert ingest.scan([emwin], set(), settle_s=0) == {"ok": 2}
    fc = TestClient(app).get("/api/discussions/active").json()
    assert [f["properties"]["label"] for f in fc["features"]] == ["MCD 1234"]  # 1201 expired
    f = fc["features"][0]
    assert f["geometry"]["type"] == "Polygon"
    assert f["properties"]["concerning"] == "Severe potential...Watch likely"
    assert f["properties"]["expires"].startswith(f"{now + timedelta(minutes=110):%Y-%m-%dT%H}")


def test_pirep_placement(db, monkeypatch):
    """Station placement at ingest, then /OV placement via a stubbed Tachbase:
    resolved and cached, airport by its K id, retried on an outage, left
    in place when unresolvable."""
    import httpx

    from satweather import pireps

    with db.begin() as conn:
        conn.execute(text("INSERT INTO source_file (name, kind, size, mtime, status) VALUES ('u', 'emwin_text', 1, now(), 'ok')"))
        pid = conn.execute(text(
            "INSERT INTO text_product (source_file_id, wmo, cccc, awips, nnn, office, received, priority, title, category, raw)"
            " SELECT id, 'UBUS01', 'KWBC', 'UABWBC', 'UAB', 'KWBC', now(), 3, 'p', 'aviation', 'r' FROM source_file"
            " RETURNING id")).scalar()
        for i, raw in enumerate(["BIH UA /OV FIM305020/TM 0153/FL220/TP B738/TB LGT",
                                 "PRB UA /OV PRB360020 /TM 0105 /FL140/TP C210",
                                 "XYZ UA /OV QQQQQ/TM 0105 /FL140/TP C210",
                                 "SAN UA /OV SAN-JLI/TM 0105 /FL140/TP C210"]):
            conn.execute(text("INSERT INTO pirep (product_id, station, observed, raw, decoded, geom)"
                              " VALUES (:p, 'X', now(), :raw, '{}', ST_SetSRID(ST_MakePoint(-100, 40), 4326))"),
                         {"p": pid, "raw": raw})

    calls = []

    class Resp:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    known = {"FIM": (34.357, -118.881), "KPRB": (35.673, -120.627), "SAN": (32.733, -117.190),
             "JLI": (33.140, -116.586)}

    def ok(url, params, **kw):
        calls.append(params["ids"].split(","))
        return Resp({"success": True, "waypoints": {
            i: {"lat": known[i][0], "lng": known[i][1], "type": "navaid", "name": i}
            for i in params["ids"].split(",") if i in known}})

    def down(url, params, **kw):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx, "get", down)
    assert pireps.place_pending(db) == {"retry": 4}
    with db.connect() as conn:
        assert conn.execute(text("SELECT max(place_tries), count(placed_at) FROM pirep")).one() == (1, 0)

    monkeypatch.setattr(httpx, "get", ok)
    assert pireps.place_pending(db) == {"placed": 3, "unplaced": 1}
    assert "KPRB" in calls[0] and "PRB" in calls[0]  # FAA id, and its ICAO form for the airport
    with db.connect() as conn:
        rows = {r[0][:3]: r[1:] for r in conn.execute(text(
            "SELECT raw, approx, round(ST_Y(geom)::numeric, 2), round(ST_X(geom)::numeric, 2) FROM pirep")).all()}
        cached = conn.execute(text("SELECT count(*), count(lat) FROM waypoint")).one()
    assert rows["XYZ"] == (True, 40, -100)  # unresolvable: stays at the station, approx
    assert rows["BIH"][0] is False and 34.4 < rows["BIH"][1] < 34.7 and -119.3 < rows["BIH"][2] < -118.9
    assert rows["PRB"][0] is False and rows["PRB"][1] > 35.9  # 20 NM north
    assert rows["SAN"][0] is True  # a route: midway, still approx
    assert cached == (5, 4)  # FIM, PRB (as KPRB), SAN, JLI known; QQQQQ remembered as a miss
    assert pireps.place_pending(db) == {} and len(calls) == 1  # all placed, nothing asked again


def test_places_search_and_brief_zone(db):
    from fastapi.testclient import TestClient

    from satweather.api.app import app

    sq = lambda x, y: f"ST_Multi(ST_GeomFromText('POLYGON(({x} {y},{x+1} {y},{x+1} {y+1},{x} {y+1},{x} {y}))', 4326))"  # noqa: E731
    with db.begin() as conn:
        for ugc, name, x, y in (("CAZ508", "SF Bay Shoreline", -123, 37), ("MIZ069", "Oakland", -84, 42)):
            conn.execute(text(f"INSERT INTO ugc_zone (kind, ugc, name, state, cwa, geom, centroid, source)"
                              f" VALUES ('public', :u, :n, 'XX', 'X', {sq(x, y)}, ST_Centroid({sq(x, y)}), 't')"),
                         {"u": ugc, "n": name})
        conn.execute(text(
            "INSERT INTO station (id, icao, faa, name, state, country, geom) VALUES"
            " ('KOAK', 'KOAK', 'OAK', 'Metro Oakland Intl', 'CA', 'US', ST_SetSRID(ST_MakePoint(-122.2, 37.7), 4326)),"
            " ('46214', NULL, NULL, 'Oakland Buoy', 'CA', 'US', ST_SetSRID(ST_MakePoint(-123.5, 37.9), 4326))"))
        conn.execute(text("INSERT INTO source_file (name, kind, size, mtime, status) VALUES ('z', 'emwin_text', 1, now(), 'ok')"))
        conn.execute(text(
            "INSERT INTO text_product (source_file_id, wmo, cccc, awips, nnn, office, received, priority, title, category, raw)"
            " SELECT id, 'FPUS56', 'KMTR', 'ZFPMTR', 'ZFP', 'MTR', now(), 3, 'ZFP', 'forecast', 'r' FROM source_file"))
        conn.execute(text(
            "INSERT INTO product_segment (product_id, seq, ugcs, headlines, body) SELECT id, 1, '{CAZ508}', '{}',"
            " E'CAZ508-241900-\\nSF Bay Shoreline-\\nIncluding the cities of San Francisco, Oakland,\\nand Daly City\\n"
            "902 PM PDT Fri Oct 23 2026\\n\\n.TONIGHT...Clear.\\n' FROM text_product"))
    c = TestClient(app)
    b = c.get("/api/zones/at", params={"lon": -122.5, "lat": 37.5, "brief": 1}).json()
    assert b["zone"]["ugc"] == "CAZ508" and b["zone"]["geometry"] and b["forecast"] is None and b["products"] == []

    def labels(**q):
        return [(r["kind"], r["label"]) for r in c.get("/api/places", params=q).json()]

    near_sf = labels(q="Oakland", near="-122.3,37.8")
    # The city (a prefix match) before the airport whose name only contains it.
    assert near_sf[:2] == [("city", "Oakland, XX"), ("station", "KOAK · Metro Oakland Intl")]
    assert ("zone", "Oakland") in near_sf and not any("Buoy" in label for _, label in near_sf)
    assert labels(q="Oakland", near="-84,42")[0] == ("zone", "Oakland")
    assert labels(q="oak")[0] == ("station", "KOAK · Metro Oakland Intl")  # the FAA id, exactly
    assert labels(q="Daly")[0] == ("city", "Daly City, XX")


def test_tropical_active_and_outlooks(db, tmp_path):
    """TCM/TCP/TWO through ingest -> tc_advisory and storm ids -> the API,
    with a meso sector footprint over the storm."""
    import json as _json

    from fastapi.testclient import TestClient

    from satweather import ingest
    from satweather.api.app import app

    names = [*FIX.glob("*-HEFZ22US.txt"), *FIX.glob("*-HEPZ32US.txt"), *FIX.glob("*-HEDZ42US.txt"),
             *FIX.glob("A_ABPZ20KNHC240525*")]
    assert _ingest(names, tmp_path) == {"ok": 4}
    proj = {"type": "geos", "altitude": 35786023.0, "lon0": -137.0, "sweep_x": True, "width": 2000, "height": 2000,
            "offset_x": 3332000.0, "offset_y": 1902000.0, "scalar_x": 501.004322, "scalar_y": -501.004322}
    with db.begin() as conn:
        # The fixtures are from 2026-10-24; make them current.
        conn.execute(text("UPDATE tc_advisory SET issued = now() - interval '1 hour'"))
        conn.execute(text("UPDATE text_product SET received = now() - interval '1 hour'"))
        conn.execute(text(
            "INSERT INTO image_frame (path, source, sector, product, kind, scan_time, size, mtime, projection, status)"
            " VALUES ('m1', 'GOES-18', 'Mesoscale 1', '13', 'band', now(), 1, now(), CAST(:p AS jsonb), 'ok')"),
            {"p": _json.dumps(proj)})
        assert conn.execute(text("SELECT count(*) FROM text_product WHERE storm_id = 'EP222026'")).scalar() == 3
    c = TestClient(app)
    d = c.get("/api/tropical/active").json()
    (s,) = d["storms"]
    assert (s["name"], s["kind"], s["number"], s["category"], s["wind_kt"], s["pressure_mb"]) == \
        ("Vance", "Hurricane", "11", "2", 95, 965)
    assert [(p["lat"], p["lon"]) for p in s["track"]] == [(11.7, -102.4), (12.0, -102.6)]  # previous, current
    assert len(s["forecast"]) == 8 and s["forecast"][0]["category"] == "3"
    assert [a["kt"] for a in s["radii_areas"]] == ["34", "50", "64"]
    assert {p["nnn"] for p in s["products"]} == {"TCM", "TCP", "TCD"}
    assert s["meso"] == ["Mesoscale 1"] and d["sectors"][0]["sector"] == "Mesoscale 1"
    o = c.get("/api/tropical/outlooks").json()
    assert [(x["basin"], [a["chance_7d"] for a in x["areas"]]) for x in o] == [("Eastern Pacific", [20])]
    assert c.get("/api/tropical/storm/ep222026").json()["history"][0]["wind_kt"] == 95


def test_signal_api(db, monkeypatch):
    from fastapi.testclient import TestClient

    from satweather import signal as sig
    from satweather.api.app import app
    from satweather.config import get_settings

    monkeypatch.setenv("SATWEATHER_SIGNAL_URL", "http://rtl.example:8081/api")
    monkeypatch.setenv("SATWEATHER_RECEIVER", '{"host": "rtl", "frequency": "1694.1e6", "bias": true}')
    get_settings.cache_clear()
    sig.store(sig.sample({"psk_demod": {"snr": 6.0, "peak_snr": 8.0, "freq": 100.0},
                          "ccsds_conv_concat_decoder": {"deframer_lock": True, "viterbi_ber": 0.02, "viterbi_lock": 1,
                                                        "rs_avg": 1}}))
    with db.begin() as conn:
        conn.execute(text("INSERT INTO signal_sample (t, ok) VALUES (now() - interval '1 minute', false)"))
        conn.execute(text("INSERT INTO source_file (name, kind, size, mtime, status) VALUES"
                          " ('a', 'emwin_text', 1, now(), 'ok'), ('b', 'emwin_image', 1, now(), 'ok')"))
    d = TestClient(app).get("/api/signal", params={"hours": 1}).json()
    assert d["configured"] and d["receiver"]["host"] == "rtl" and d["receiver"]["bias"] is True
    assert d["latest"]["ok"] and d["latest"]["snr"] == 6.0 and d["last_ok"] == d["latest"]["t"]
    assert sum(h["n"] for h in d["history"]) == 2 and d["bucket_s"] == 60
    assert [(x["text"], x["graphics"], x["images"]) for x in d["throughput"]] == [(1, 1, 0)]
