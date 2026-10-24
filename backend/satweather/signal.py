"""`satweather-signal`: the receiver's live signal, from SatDump's stats.

SatDump, started with --http_server, serves its modules' live stats as
JSON at /api. For the goes_hrit pipeline:

    {"psk_demod": {"snr": 6.1, "peak_snr": 8.0, "freq": -1234.5},
     "ccsds_conv_concat_decoder": {"viterbi_ber": 0.02, "viterbi_lock": 1,
                                   "deframer_lock": true, "rs_avg": 2}}

Every POLL_S this stores one sample (ok = false when the server did not
answer) and announces it on the event stream for the Signal page.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal as signals
import time

import httpx
from sqlalchemy import text

from satweather.config import get_settings
from satweather.db import get_engine
from satweather.store import notify

log = logging.getLogger("satweather.signal")

POLL_S = 10
_stop = False


def _handle_stop(*_):
    global _stop
    _stop = True


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def sample(stats: dict | None) -> dict:
    """SatDump's stats JSON -> one signal_sample row (None: no answer)."""
    if stats is None:
        return {"ok": False, "snr": None, "peak_snr": None, "freq_hz": None, "viterbi_ber": None,
                "viterbi_lock": None, "deframer_lock": None, "rs_avg": None, "raw": None}
    demod = stats.get("psk_demod") or {}
    dec = stats.get("ccsds_conv_concat_decoder") or {}
    lock = dec.get("viterbi_lock")
    rs = _num(dec.get("rs_avg"))
    return {
        "ok": True,
        "snr": _num(demod.get("snr")), "peak_snr": _num(demod.get("peak_snr")), "freq_hz": _num(demod.get("freq")),
        "viterbi_ber": _num(dec.get("viterbi_ber")),
        "viterbi_lock": bool(lock) if lock is not None else None,
        "deframer_lock": bool(dec["deframer_lock"]) if "deframer_lock" in dec else None,
        # SatDump reports -1 for a frame Reed-Solomon could not correct.
        "rs_avg": rs,
        "raw": stats,
    }


def poll(c: httpx.Client, url: str) -> dict | None:
    try:
        r = c.get(url, timeout=3)
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError):
        return None


def store(row: dict) -> None:
    with get_engine().begin() as conn:
        t = conn.execute(text(
            """
            INSERT INTO signal_sample (ok, snr, peak_snr, freq_hz, viterbi_ber, viterbi_lock, deframer_lock, rs_avg, raw)
            VALUES (:ok, :snr, :peak_snr, :freq_hz, :viterbi_ber, :viterbi_lock, :deframer_lock, :rs_avg,
                    CAST(:raw AS jsonb))
            ON CONFLICT (t) DO NOTHING RETURNING t
            """), {**row, "raw": json.dumps(row["raw"]) if row["raw"] is not None else None}).scalar()
        if t:
            notify(conn, {"type": "signal", "t": t.isoformat(), **{k: v for k, v in row.items() if k != "raw"}})


def main() -> None:
    ap = argparse.ArgumentParser(prog="satweather-signal")
    ap.add_argument("--once", action="store_true", help="one poll, printed, nothing stored")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one request every POLL_S
    signals.signal(signals.SIGTERM, _handle_stop)
    signals.signal(signals.SIGINT, _handle_stop)
    url = get_settings().signal_url
    if not url:
        log.warning("SATWEATHER_SIGNAL_URL is not set; nothing to poll")
    with httpx.Client(headers={"User-Agent": "satweather (signal)"}) as c:
        if args.once:
            print(json.dumps(sample(poll(c, url)) if url else {}, indent=2))
            return
        was_ok = None
        while not _stop:
            t0 = time.time()
            if url:
                row = sample(poll(c, url))
                if row["ok"] != was_ok:
                    log.info("receiver stats %s", "answering" if row["ok"] else f"not answering at {url}")
                    was_ok = row["ok"]
                try:
                    store(row)
                except Exception:
                    log.exception("could not store a sample")
            while not _stop and time.time() - t0 < POLL_S:
                time.sleep(0.2)
    log.info("stopped")


if __name__ == "__main__":
    main()
