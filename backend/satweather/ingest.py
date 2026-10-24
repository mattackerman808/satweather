"""`satweather-ingest`: watch the satImages share and store what arrives.

NFS gives a client no inotify for files another client writes, so this
polls: every few seconds it lists EMWIN/ and takes every file it has not
seen that has *settled*: non-empty and not modified for SETTLE_S. satdump
creates files before it fills them, so a fresh 0-byte file is normal and
must not be ingested yet.

satweather-housekeep moves files out of EMWIN/ after a few hours, which keeps
the listing small. The same loop catalogs satellite images under IMAGES/
(satweather.imagery) for satweather-imaging to convert. `--backfill` also walks EMWIN/archive/*/, for catching
up after this service was down longer than that.

Each file is one transaction. A file that fails is recorded with its
error and not retried, so one bad product cannot wedge the loop.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from satweather.config import get_settings
from satweather.db import get_engine
from satweather.emwin import normalize, parse_name
from satweather.imagery import Scanner, from_emwin, store_image
from satweather.imagery import load_seen as load_seen_images
from satweather.parse import parse
from satweather.store import store_other, store_text

log = logging.getLogger("satweather.ingest")

SETTLE_S = 20
SCAN_S = 10
SEEN_DAYS = 3

_stop = False


def _handle_stop(*_):
    global _stop
    _stop = True


def load_seen(conn) -> set[str]:
    rows = conn.execute(
        text("SELECT name FROM source_file WHERE ingested_at > now() - make_interval(days => :d)"),
        {"d": SEEN_DAYS},
    )
    return {r[0] for r in rows}


def ingest_file(path: Path, st: os.stat_result) -> str:
    """Store one settled file. Returns the status recorded."""
    name = path.name
    en = parse_name(name)
    engine = get_engine()
    with engine.begin() as conn:
        if en is None:
            store_other(conn, name=name, kind="unknown", size=st.st_size, mtime=st.st_mtime, en=None,
                        status="skipped", error="filename does not match the EMWIN pattern")
            return "skipped"
        if not en.is_text:
            store_other(conn, name=name, kind="emwin_image", size=st.st_size, mtime=st.st_mtime, en=en,
                        status="ok")
            rel = os.path.relpath(path, get_settings().data_root)
            store_image(conn, rel, from_emwin(en.awips_long, en.received), st.st_size, st.st_mtime)
            return "image"
        raw = normalize(path.read_bytes(), en)
        parsed = parse(raw, en, datetime.now(UTC))
        store_text(conn, name=name, size=st.st_size, mtime=st.st_mtime, en=en, raw=raw, parsed=parsed)
        return "raw" if parsed.error else "ok"


def _record_error(path: Path, st: os.stat_result, exc: Exception) -> None:
    try:
        with get_engine().begin() as conn:
            store_other(conn, name=path.name, kind="emwin_text", size=st.st_size, mtime=st.st_mtime,
                        en=parse_name(path.name), status="error", error=f"{type(exc).__name__}: {exc}"[:500])
    except Exception:
        log.exception("could not record failure for %s", path.name)


def scan(dirs: list[Path], seen: set[str], settle_s: float = SETTLE_S) -> dict[str, int]:
    counts: dict[str, int] = {}
    now = time.time()
    for d in dirs:
        try:
            entries = list(os.scandir(d))
        except OSError as exc:
            log.warning("cannot list %s: %s", d, exc)
            continue
        # Oldest first, so a backlog lands in the order it was received.
        entries.sort(key=lambda e: e.name.split("_C_KWIN_", 1)[-1])
        for e in entries:
            if _stop:
                return counts
            if e.name in seen or not e.is_file(follow_symlinks=False):
                continue
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            if st.st_size == 0 or now - st.st_mtime < settle_s:
                continue
            path = Path(e.path)
            try:
                status = ingest_file(path, st)
            except Exception as exc:
                log.exception("failed: %s", e.name)
                _record_error(path, st, exc)
                status = "error"
            seen.add(e.name)
            counts[status] = counts.get(status, 0) + 1
    return counts


def redecode() -> dict[str, int]:
    """Fill decoded METAR fields, TAFs and PIREPs for products stored
    before those decoders existed. Idempotent: inserts ignore duplicates."""
    from satweather.reports import PIREP_NNN, TAF_NNN, extract, metar
    from satweather.store import store_reports

    counts = {"metar": 0, "products": 0}
    engine = get_engine()
    with engine.begin() as conn:
        for mid, raw in conn.execute(text("SELECT id, raw FROM metar WHERE decoded IS NULL")).all():
            try:
                d, cat = metar(raw)
            except Exception:
                continue
            conn.execute(text("UPDATE metar SET decoded = CAST(:d AS jsonb), flight_category = :c WHERE id = :i"),
                         {"d": json.dumps(d), "c": cat, "i": mid})
            counts["metar"] += 1
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, nnn, raw, received FROM text_product WHERE nnn = ANY(:n) OR raw LIKE '% UA /OV%'"),
            {"n": sorted(TAF_NNN | PIREP_NNN)},
        ).all()
    for pid, nnn, raw, received in rows:
        reps = extract(nnn, raw, received)
        if reps:
            with engine.begin() as conn:
                store_reports(conn, pid, reps)
            counts["products"] += 1
    return counts


def graphics_backfill(days: int) -> int:
    """Catalog EMWIN graphics ingested before image_frame existed, from
    the EMWIN archive, so radar loops have history from the start."""
    from satweather.emwin import archived_path

    root = get_settings().data_root
    n = 0
    with get_engine().begin() as conn:
        rows = conn.execute(text(
            "SELECT name, size, mtime FROM source_file WHERE kind = 'emwin_image' AND status = 'ok'"
            " AND mtime > now() - make_interval(days => :d)"), {"d": days}).all()
        for name, size, mtime in rows:
            en = parse_name(name)
            # Still in EMWIN/ if it arrived in the last few hours.
            rel = next((r for r in (f"EMWIN/{name}", str(archived_path(name, mtime.timestamp())))
                        if (root / r).exists()), None)
            if en is None or rel is None:
                continue
            n += store_image(conn, rel, from_emwin(en.awips_long, en.received), size, mtime.timestamp())
    return n


def main() -> None:
    ap = argparse.ArgumentParser(prog="satweather-ingest")
    ap.add_argument("--once", action="store_true", help="one scan, then exit")
    ap.add_argument("--backfill", action="store_true", help="also walk EMWIN/archive/*/ (implies --once)")
    ap.add_argument("--graphics-backfill", type=int, metavar="DAYS",
                    help="catalog EMWIN graphics from the last DAYS (live or archived) for imaging, then exit")
    ap.add_argument("--redecode", action="store_true",
                    help="re-run the METAR/TAF/PIREP decoders over stored products, then exit")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    if args.graphics_backfill:
        log.info("graphics backfill: %d catalogued", graphics_backfill(args.graphics_backfill))
        return

    if args.redecode:
        log.info("redecode: %s", redecode())
        return

    emwin = get_settings().data_root / "EMWIN"
    with get_engine().connect() as conn:
        seen = load_seen(conn)
    log.info("starting: %d files seen in the last %d days", len(seen), SEEN_DAYS)

    if args.backfill:
        archive = sorted((emwin / "archive").glob("*/")) if (emwin / "archive").is_dir() else []
        with get_engine().connect() as conn:
            seen |= {r[0] for r in conn.execute(text("SELECT name FROM source_file"))}
        log.info("backfill: %d archive days", len(archive))
        log.info("backfill done: %s", scan([*archive, emwin], seen))
        return

    with get_engine().connect() as conn:
        images = Scanner(get_settings().data_root, load_seen_images(conn, SEEN_DAYS))
    last_prune = time.time()
    while not _stop:
        t0 = time.time()
        counts = scan([emwin], seen)
        if counts:
            log.info("ingested %s in %.1fs", counts, time.time() - t0)
        t0 = time.time()
        try:
            if n := images.scan(get_engine().begin, SETTLE_S, stop=lambda: _stop):
                log.info("catalogued %d images in %.1fs", n, time.time() - t0)
        except Exception:
            log.exception("image scan failed")
        if args.once:
            return
        # Forget names rtl has archived long ago, so the sets stay bounded.
        if time.time() - last_prune > 6 * 3600:
            with get_engine().connect() as conn:
                seen = load_seen(conn)
                images.seen = load_seen_images(conn, SEEN_DAYS)
            last_prune = time.time()
        for _ in range(SCAN_S * 10):
            if _stop:
                break
            time.sleep(0.1)
    log.info("stopped")


if __name__ == "__main__":
    main()
