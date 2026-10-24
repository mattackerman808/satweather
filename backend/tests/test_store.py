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
        n = conn.execute(text("SELECT count(*) FROM text_product")).scalar()
        raw = conn.execute(text("SELECT count(*) FROM text_product WHERE parse_error IS NOT NULL")).scalar()
        metars = conn.execute(text("SELECT count(*) FROM metar")).scalar()
    assert n == len(list(FIX.iterdir())) and raw <= 4 and metars > 0


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
