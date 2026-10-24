import os
import time

from fastapi.testclient import TestClient

from satweather.api import app as app_module
from satweather.config import get_settings


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("SATWEATHER_DATA_ROOT", str(tmp_path))
    # Nothing listens here: the DB check must fail cleanly, not raise.
    monkeypatch.setenv("SATWEATHER_DATABASE_URL", "postgresql+psycopg://x@127.0.0.1:1/x")
    get_settings.cache_clear()
    app_module.get_engine.cache_clear()
    return TestClient(app_module.app)


def test_health_degraded_without_db_or_feed(tmp_path, monkeypatch):
    body = _client(tmp_path, monkeypatch).get("/api/health").json()
    assert body["status"] == "degraded"
    assert body["db"]["ok"] is False
    assert body["feed"]["ok"] is False


def test_health_reports_feed_age(tmp_path, monkeypatch):
    emwin = tmp_path / "EMWIN"
    emwin.mkdir()
    f = emwin / "A_TEST.txt"
    f.write_text("x")
    old = time.time() - 120
    os.utime(f, (old, old))
    body = _client(tmp_path, monkeypatch).get("/api/health").json()
    assert body["feed"]["ok"] is True
    assert 100 <= body["feed"]["newest_age_s"] <= 200
