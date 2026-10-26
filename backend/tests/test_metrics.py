import os
import time

from fastapi.testclient import TestClient

from satweather.api import app as app_module
from satweather.api.metrics import render
from satweather.config import get_settings


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("SATWEATHER_DATA_ROOT", str(tmp_path))
    # Nothing listens here: the scrape must still answer.
    monkeypatch.setenv("SATWEATHER_DATABASE_URL", "postgresql+psycopg://x@127.0.0.1:1/x")
    get_settings.cache_clear()
    app_module.get_engine.cache_clear()
    return TestClient(app_module.app)


def _parse(body: str) -> dict[str, float]:
    return {k: float(v) for k, v in (l.split() for l in body.splitlines() if l and not l.startswith("#"))}


def test_metrics_without_db_or_feed(tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch).get("/api/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    m = _parse(r.text)
    assert m == {"satweather_share_up": 0.0, "satweather_db_up": 0.0}


def test_metrics_feed_age(tmp_path, monkeypatch):
    emwin = tmp_path / "EMWIN"
    emwin.mkdir()
    f = emwin / "A_TEST.txt"
    f.write_text("x")
    old = time.time() - 120
    os.utime(f, (old, old))
    m = _parse(_client(tmp_path, monkeypatch).get("/api/metrics").text)
    assert m["satweather_share_up"] == 1
    assert 100 <= m["satweather_feed_newest_age_seconds"] <= 200


def test_render_skips_missing_values():
    out = render({"a": ("A.", 1), "b": ("B.", None), "c": ("C.", 0.25)})
    assert out == "# HELP a A.\n# TYPE a gauge\na 1.0\n# HELP c C.\n# TYPE c gauge\nc 0.25\n"
