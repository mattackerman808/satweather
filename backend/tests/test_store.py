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
