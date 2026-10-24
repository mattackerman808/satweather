"""The receiver's signal (SatDump's live stats, polled by satweather-signal)
and what actually came through it."""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import text

from satweather.config import get_settings
from satweather.db import get_engine

router = APIRouter(prefix="/api")

COLS = "t, ok, snr, peak_snr, freq_hz, viterbi_ber, viterbi_lock, deframer_lock, rs_avg"


@router.get("/signal")
def signal(hours: float = Query(24, gt=0, le=24 * 30)) -> dict:
    """Latest sample, the history in about 288 buckets, and per hour the
    files SatDump wrote: EMWIN text and graphics, satellite images."""
    s = get_settings()
    bucket = max(60, round(hours * 3600 / 288))
    p = {"secs": hours * 3600, "b": bucket}
    with get_engine().connect() as conn:
        latest = conn.execute(text(f"SELECT {COLS} FROM signal_sample ORDER BY t DESC LIMIT 1")).mappings().first()
        last_ok = conn.execute(text("SELECT max(t) FROM signal_sample WHERE ok")).scalar()
        # The last minute of answered samples: a single 10 s reading is
        # noisy at low SNR (SatDump's estimate bottoms out at 0.0 dB).
        recent = conn.execute(text(f"""
            SELECT {COLS} FROM signal_sample WHERE ok AND t > now() - interval '1 minute' ORDER BY t""")).mappings().all()
        history = conn.execute(text("""
            SELECT to_timestamp(floor(extract(epoch FROM t) / :b) * :b) AS t,
                   avg(snr) AS snr, min(snr) AS snr_min, max(snr) AS snr_max, max(peak_snr) AS peak_snr,
                   avg(viterbi_ber) AS ber, avg(freq_hz) AS freq_hz,
                   avg(CASE WHEN deframer_lock THEN 1.0 ELSE 0.0 END) FILTER (WHERE ok) AS locked,
                   avg(CASE WHEN ok THEN 1.0 ELSE 0.0 END) AS reachable, count(*) AS n
            FROM signal_sample WHERE t > now() - make_interval(secs => :secs)
            GROUP BY 1 ORDER BY 1"""), p).mappings().all()
        files = {r["h"]: dict(r) for r in conn.execute(text("""
            SELECT date_trunc('hour', mtime) AS h,
                   count(*) FILTER (WHERE kind = 'emwin_text') AS text,
                   count(*) FILTER (WHERE kind = 'emwin_image') AS graphics
            FROM source_file WHERE mtime > now() - make_interval(secs => :secs) GROUP BY 1"""), p).mappings()}
        for r in conn.execute(text("""
            SELECT date_trunc('hour', mtime) AS h, count(*) AS images FROM image_frame
            WHERE source <> 'NWS' AND mtime > now() - make_interval(secs => :secs) GROUP BY 1"""), p).mappings():
            files.setdefault(r["h"], {"h": r["h"], "text": 0, "graphics": 0})["images"] = r["images"]
    return {
        "configured": bool(s.signal_url),
        "receiver": s.receiver,
        "latest": dict(latest) if latest else None,
        "recent": [dict(r) for r in recent],
        "last_ok": last_ok,
        "bucket_s": bucket,
        "history": [dict(r) for r in history],
        "throughput": [{"images": 0, **f} for _, f in sorted(files.items())],
    }
