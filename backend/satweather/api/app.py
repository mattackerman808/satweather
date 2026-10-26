"""FastAPI application. Apache proxies /api/ here (uvicorn on 127.0.0.1:8400)."""

import time

from fastapi import FastAPI
from sqlalchemy import text

from satweather.api import discussions, events, imagery, metrics, offices, products, signal, statements, stations, tropical, warnings, zones
from satweather.api.metrics import newest_mtime
from satweather.config import get_settings
from satweather.db import get_engine

app = FastAPI(title="satweather", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.include_router(products.router)
app.include_router(warnings.router)
app.include_router(events.router)
app.include_router(stations.router)
app.include_router(imagery.router)
app.include_router(zones.router)
app.include_router(discussions.router)
app.include_router(tropical.router)
app.include_router(offices.router)
app.include_router(statements.router)
app.include_router(signal.router)
app.include_router(metrics.router)


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

    newest = newest_mtime(settings.data_root)
    if newest is None:
        out["feed"] = {"ok": False, "error": f"no data under {settings.data_root}"}
        out["status"] = "degraded"
    else:
        age = round(time.time() - newest)
        out["feed"] = {"ok": age < 900, "newest_age_s": age}
        if age >= 900:
            out["status"] = "degraded"

    return out
