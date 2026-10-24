"""Server-sent events: one message per product or image as it is ingested.

The ingest worker calls pg_notify('satweather', ...) inside each file's
transaction; this endpoint LISTENs on its own connection per client and
forwards the payloads. A comment line every HEARTBEAT_S keeps proxies
from timing the stream out and lets a dropped client be noticed.
"""

from __future__ import annotations

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.engine import make_url

from satweather.config import get_settings
from satweather.store import CHANNEL

router = APIRouter(prefix="/api")

HEARTBEAT_S = 20


def _conninfo() -> str:
    url = make_url(get_settings().database_url).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    async def stream():
        async with await psycopg.AsyncConnection.connect(_conninfo(), autocommit=True) as conn:
            await conn.execute(f"LISTEN {CHANNEL}")
            yield "retry: 5000\n\n"
            while not await request.is_disconnected():
                got = False
                async for n in conn.notifies(timeout=HEARTBEAT_S):
                    got = True
                    yield f"data: {n.payload}\n\n"
                if not got:
                    yield ": keepalive\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
