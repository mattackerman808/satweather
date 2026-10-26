"""Prometheus metrics for the fleet's Grafana alerts (salt: Alloy scrapes
127.0.0.1:8400/api/metrics on satweather, rules in alert-rules-satweather).

Each gauge answers one question an alert asks: is rtl still writing, is
ingest keeping up, is imaging keeping up, does the receiver have lock.
Ages are seconds. A gauge whose source is unavailable is left out rather
than faked, and the matching *_up gauge says why.
"""

from __future__ import annotations

import os
import time

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from sqlalchemy import text

from satweather.config import get_settings
from satweather.db import get_engine

router = APIRouter(prefix="/api")

# Window for the signal ratios: long enough that one dropped 10 s poll or
# a low-SNR dip doesn't swing them, short enough to follow a real loss.
SIGNAL_WINDOW_S = 600


def newest_mtime(root, subs=("EMWIN", "IMAGES"), max_entries: int = 5000) -> float | None:
    """Newest mtime among the entries directly under each of `subs`.

    Cheap on purpose: health and metrics are polled. EMWIN is the busiest
    directory (~1,200 files an hour), so its newest entry says whether
    the dish and decoder on rtl are still delivering.
    """
    newest = None
    for sub in subs:
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


# name -> (help, value). Ordered as emitted.
def _collect() -> dict[str, tuple[str, float | None]]:
    out: dict[str, tuple[str, float | None]] = {}
    now = time.time()

    newest = newest_mtime(get_settings().data_root, subs=("EMWIN",))
    out["satweather_share_up"] = ("1 if EMWIN/ on the share has any entries.", 0 if newest is None else 1)
    out["satweather_feed_newest_age_seconds"] = (
        "Age of the newest file rtl wrote to EMWIN/ on the share.",
        None if newest is None else max(0.0, now - newest),
    )

    try:
        with get_engine().connect() as conn:
            row = conn.execute(text("""
                SELECT
                  (SELECT extract(epoch FROM now() - ingested_at) FROM source_file
                    ORDER BY id DESC LIMIT 1) AS ingest_age,
                  (SELECT extract(epoch FROM now() - mtime) FROM image_frame
                    WHERE source <> 'NWS' ORDER BY id DESC LIMIT 1) AS image_age,
                  (SELECT count(*) FROM image_frame WHERE status = 'pending') AS pending,
                  (SELECT extract(epoch FROM now() - min(catalogued_at)) FROM image_frame
                    WHERE status = 'pending') AS pending_age,
                  (SELECT extract(epoch FROM now() - max(t)) FROM signal_sample) AS signal_age
            """)).mappings().one()
            sig = conn.execute(text("""
                SELECT count(*) AS n,
                       avg(CASE WHEN ok THEN 1.0 ELSE 0.0 END) AS answered,
                       avg(CASE WHEN deframer_lock THEN 1.0 ELSE 0.0 END) FILTER (WHERE ok) AS locked,
                       avg(snr) FILTER (WHERE ok) AS snr,
                       count(DISTINCT (snr, freq_hz)) FILTER (WHERE ok) AS distinct_readings
                FROM signal_sample WHERE t > now() - make_interval(secs => :w)"""), {"w": SIGNAL_WINDOW_S}).mappings().one()
    except Exception:  # report, don't raise: a scrape must answer
        out["satweather_db_up"] = ("1 if the database answered.", 0)
        return out

    out["satweather_db_up"] = ("1 if the database answered.", 1)
    out["satweather_ingest_last_age_seconds"] = ("Seconds since ingest last stored a file.", row["ingest_age"])
    out["satweather_image_newest_age_seconds"] = ("Age (mtime) of the satellite image catalogued last.", row["image_age"])
    out["satweather_imaging_pending"] = ("Image frames waiting for display copies.", row["pending"])
    out["satweather_imaging_oldest_pending_age_seconds"] = (
        "Seconds the oldest pending image frame has waited (0 when none).",
        row["pending_age"] if row["pending_age"] is not None else 0,
    )
    out["satweather_signal_sample_age_seconds"] = ("Seconds since satweather-signal stored a sample.", row["signal_age"])
    w = f"over the last {SIGNAL_WINDOW_S // 60} min"
    out["satweather_signal_samples"] = (f"Signal samples stored {w}.", sig["n"])
    out["satweather_signal_answered_ratio"] = (f"Fraction of polls rtl's SatDump stats answered {w}.", sig["answered"])
    out["satweather_signal_locked_ratio"] = (f"Fraction of answered polls with deframer lock {w}.", sig["locked"])
    out["satweather_signal_snr_db"] = (f"Mean SNR {w}.", sig["snr"])
    # SatDump keeps serving its last readings after the dongle drops off
    # USB (2026-10-26: one SNR/offset pair for two hours, deframer
    # "locked" throughout). The carrier offset moves on every live poll,
    # so a window of answered polls with one distinct reading is frozen.
    answered = round(sig["n"] * (sig["answered"] or 0))
    out["satweather_signal_frozen"] = (
        f"1 if rtl's SatDump stats answered but never changed {w} (the decoder has stopped).",
        None if answered < 10 else int(sig["distinct_readings"] <= 1),
    )
    return out


def render(metrics: dict[str, tuple[str, float | None]]) -> str:
    lines = []
    for name, (help_, value) in metrics.items():
        if value is None:
            continue
        lines += [f"# HELP {name} {help_}", f"# TYPE {name} gauge", f"{name} {float(value)!r}"]
    return "\n".join(lines) + "\n"


@router.get("/metrics", response_class=PlainTextResponse)
def metrics() -> PlainTextResponse:
    return PlainTextResponse(render(_collect()), media_type="text/plain; version=0.0.4")
