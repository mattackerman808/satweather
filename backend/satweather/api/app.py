"""FastAPI application. Apache proxies /api/ here (uvicorn on 127.0.0.1:8400)."""

import os
import time

from fastapi import FastAPI
from sqlalchemy import text

from satweather.api import events, imagery, products, stations, warnings, zones
from satweather.config import get_settings
from satweather.db import get_engine

app = FastAPI(title="satweather", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.include_router(products.router)
app.include_router(warnings.router)
app.include_router(events.router)
app.include_router(stations.router)
app.include_router(imagery.router)
app.include_router(zones.router)


def _newest_mtime(root, max_entries: int = 5000) -> float | None:
    """Newest mtime among the top-level product dirs and EMWIN files.

    Cheap on purpose: health is polled. EMWIN is the busiest directory
    (~1,200 files an hour), so its newest entry says whether the dish
    and decoder on rtl are still delivering.
    """
    newest = None
    for sub in ("EMWIN", "IMAGES"):
        path = root / sub
        try:
            with os.scandir(path) as it:
                for i, entry in enumerate(it):
                    if i >= max_entries:
                        break
                    m = entry.stat(follow_symlinks=False).st_mtime
                    if newest is None or m > newest:
                        newest = m
        except OSError:
            continue
    return newest


@app.get("/api/health")
def health() -> dict:
    settings = get_settings()
    out: dict = {"status": "ok"}

    try:
        with get_engine().connect() as conn:
            out["db"] = {
                "ok": True,
                "postgis": conn.execute(text("SELECT postgis_lib_version()")).scalar(),
            }
            # Seconds since the ingest worker last stored anything. None
            # before the first file (or before migration 0002).
            lag = conn.execute(
                text(
                    "SELECT extract(epoch FROM now() - max(ingested_at))::int FROM source_file"
                    " WHERE ingested_at > now() - interval '1 day'"
                    if conn.execute(text("SELECT to_regclass('source_file')")).scalar()
                    else "SELECT NULL"
                )
            ).scalar()
            out["ingest"] = {"ok": lag is not None and lag < 600, "last_age_s": lag}
            if not out["ingest"]["ok"]:
                out["status"] = "degraded"
    except Exception as exc:  # report, don't raise: health must answer
        out["db"] = {"ok": False, "error": type(exc).__name__}
        out["status"] = "degraded"

    newest = _newest_mtime(settings.data_root)
    if newest is None:
        out["feed"] = {"ok": False, "error": f"no data under {settings.data_root}"}
        out["status"] = "degraded"
    else:
        age = round(time.time() - newest)
        out["feed"] = {"ok": age < 900, "newest_age_s": age}
        if age >= 900:
            out["status"] = "degraded"

    return out
