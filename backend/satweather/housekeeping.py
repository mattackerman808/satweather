"""`satweather-housekeep`: housekeeping and retention for rtl's output and
the web copies. A timer runs it every 10 minutes.

satweather is the only thing that moves or deletes in data_root; rtl only
writes. (The NFS export is all_squash to the satweather user, so every
file is ours to move or delete.) Three jobs, each idempotent:

1. EMWIN archiving. satdump never prunes EMWIN/ (~26,000 files a day),
   and ingest lists it every few seconds, so files older than
   emwin_archive_hours move to EMWIN/archive/<UTC date of mtime>/, the
   rule satweather.emwin.archived_path reads back. Only files ingest has
   recorded move: if ingest is down, nothing is hidden from it.
2. Originals (keep_originals_days; 0 keeps them forever): SatDump scan
   directories under IMAGES/ and L2/, LRIT charts in IMAGES/NWS/, and
   whole days of EMWIN/archive/. Only names that parse as one of those
   are ever deleted.
3. Copies (keep_copies_days): image_frame rows and their WebPs; and
   receiver signal samples (keep_signal_days).

--dry-run reports what each job would do and changes nothing.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from satweather.config import get_settings
from satweather.db import get_engine
from satweather.emwin import archived_path
from satweather.imagery import lrit_time, scan_dir_time

log = logging.getLogger("satweather.housekeep")


def archive_emwin(root: Path, hours: float, dry: bool = False) -> int:
    """Move ingested EMWIN files older than `hours` into the archive."""
    emwin = root / "EMWIN"
    cutoff = time.time() - hours * 3600
    old: list[tuple[str, float]] = []
    for e in os.scandir(emwin):
        try:
            if e.is_file(follow_symlinks=False) and (m := e.stat(follow_symlinks=False).st_mtime) < cutoff:
                old.append((e.name, m))
        except OSError:
            continue
    if not old:
        return 0
    with get_engine().connect() as conn:
        known = {r[0] for r in conn.execute(text("SELECT name FROM source_file WHERE name = ANY(:n)"),
                                            {"n": [n for n, _ in old]})}
    moved = 0
    for name, mtime in old:
        if name not in known:
            continue
        dst = root / archived_path(name, mtime)
        if dst.exists():
            continue
        if not dry:
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.rename(emwin / name, dst)
            except FileNotFoundError:
                continue
        moved += 1
    return moved


def _scan_dirs(top: Path):
    """SatDump scan directories under IMAGES/ or L2/: <sat>/<scan> or
    <sat>/<sector>/<scan>, for any satellite directory."""
    try:
        sats = [e for e in os.scandir(top) if e.is_dir(follow_symlinks=False)]
    except OSError:
        return
    for sat in sats:
        try:
            subs = [e for e in os.scandir(sat.path) if e.is_dir(follow_symlinks=False)]
        except OSError:
            continue
        for e in subs:
            if scan_dir_time(e.name):
                yield e
                continue
            try:
                yield from (s for s in os.scandir(e.path) if s.is_dir(follow_symlinks=False) and scan_dir_time(s.name))
            except OSError:
                continue


def prune_originals(root: Path, days: int, dry: bool = False) -> dict[str, int]:
    """Delete originals older than `days` from the share. 0: keep all."""
    out = {"scans": 0, "charts": 0, "emwin_days": 0}
    if days <= 0:
        return out
    cutoff = datetime.now(UTC) - timedelta(days=days)
    for top in ("IMAGES", "L2"):
        for d in list(_scan_dirs(root / top)):
            if scan_dir_time(d.name) < cutoff:
                if not dry:
                    shutil.rmtree(d.path, ignore_errors=True)
                out["scans"] += 1
    try:
        for f in os.scandir(root / "IMAGES" / "NWS"):
            if (t := lrit_time(f.name)) and t < cutoff and f.is_file(follow_symlinks=False):
                if not dry:
                    os.unlink(f.path)
                out["charts"] += 1
    except OSError:
        pass
    try:
        for d in os.scandir(root / "EMWIN" / "archive"):
            try:
                day = date.fromisoformat(d.name)
            except ValueError:
                continue
            # A whole day, once all of it is past the cutoff.
            if day < cutoff.date() and d.is_dir(follow_symlinks=False):
                if not dry:
                    shutil.rmtree(d.path, ignore_errors=True)
                out["emwin_days"] += 1
    except OSError:
        pass
    return out


def prune_copies(media_root: Path, days: int, dry: bool = False) -> int:
    """Delete image_frame rows older than `days` and their WebPs."""
    if days <= 0:
        return 0
    sql = "FROM image_frame WHERE scan_time < now() - make_interval(days => :d)"
    with get_engine().begin() as conn:
        if dry:
            return conn.execute(text(f"SELECT count(*) {sql}"), {"d": days}).scalar()
        rows = conn.execute(text(f"DELETE {sql} RETURNING display, thumb"), {"d": days}).all()
    parents = set()
    for row in rows:
        for rel in filter(None, row):
            (media_root / rel).unlink(missing_ok=True)
            parents.add((media_root / rel).parent)
    for d in parents:  # the scan's directory, once its last copy is gone
        try:
            d.rmdir()
        except OSError:
            pass
    return len(rows)


def prune_signal(days: int, dry: bool = False) -> int:
    """Delete receiver signal samples older than `days`."""
    if days <= 0:
        return 0
    sql = "FROM signal_sample WHERE t < now() - make_interval(days => :d)"
    with get_engine().begin() as conn:
        if dry:
            return conn.execute(text(f"SELECT count(*) {sql}"), {"d": days}).scalar()
        return conn.execute(text(f"DELETE {sql}"), {"d": days}).rowcount


def main() -> None:
    ap = argparse.ArgumentParser(prog="satweather-housekeep")
    ap.add_argument("--dry-run", action="store_true", help="report what would be done, change nothing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    s = get_settings()
    dry = " (dry run)" if args.dry_run else ""
    failed = False
    jobs = [
        ("emwin archived", lambda: archive_emwin(s.data_root, s.emwin_archive_hours, args.dry_run)),
        ("originals deleted", lambda: prune_originals(s.data_root, s.keep_originals_days, args.dry_run)),
        ("copies deleted", lambda: prune_copies(s.media_root, s.keep_copies_days, args.dry_run)),
        ("signal samples deleted", lambda: prune_signal(s.keep_signal_days, args.dry_run)),
    ]
    for what, job in jobs:
        try:
            log.info("%s: %s%s", what, job(), dry)
        except Exception:
            log.exception("%s: failed", what)
            failed = True
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
