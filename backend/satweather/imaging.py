"""`satweather-imaging`: web-sized copies of the images ingest catalogued.

Each pending image_frame row is claimed with FOR UPDATE SKIP LOCKED and
held for the conversion, so the row is the job: a crash rolls the claim
back and the row is pending again. Newest first, so after downtime the
live view is current at once and the backlog fills in behind it.

Copies go to <media_root>/sat/<satellite>/<sector>/<scan>/<product>.webp
(plus a _t.webp thumbnail), written to a temporary name and renamed, so
Apache never serves half a file. An original the share has already
pruned is marked gone; any other failure is recorded and not retried.
"""

from __future__ import annotations

import argparse
import logging
import re
import signal
import time
from pathlib import Path

from sqlalchemy import text

from satweather.config import get_settings
from satweather.db import get_engine
from satweather.store import notify

log = logging.getLogger("satweather.imaging")

# Full Disk is 5424 px square; 2048 keeps a loop of them light enough for
# a browser. Meso sectors (2000 px) pass through at native size.
DISPLAY_PX = 2048
THUMB_PX = 320
IDLE_S = 2.0

_stop = False


def _handle_stop(*_):
    global _stop
    _stop = True


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def media_rel(satellite: str, sector: str, scan_time, product: str, overlay: bool) -> str:
    stem = _slug(product) + ("_map" if overlay else "")
    return f"sat/{_slug(satellite)}/{_slug(sector)}/{scan_time:%Y%m%dT%H%M%SZ}/{stem}"


def _save(img, dst: Path, q: int) -> None:
    tmp = dst.with_name(dst.name + ".tmp")
    img.webpsave(str(tmp), Q=q, effort=2, strip=True)
    tmp.rename(dst)


def make_copies(src: Path, media_root: Path, rel: str) -> tuple[str, str, int, int]:
    """Display + thumbnail WebPs for one original. Returns their paths
    (relative to media_root) and the display size."""
    import pyvips

    # Rendered once into memory (at most 2048² x 4 bytes): both saves
    # read it, instead of each decoding the PNG again.
    img = pyvips.Image.thumbnail(str(src), DISPLAY_PX, size="down").copy_memory()
    display = media_root / f"{rel}.webp"
    thumb = media_root / f"{rel}_t.webp"
    display.parent.mkdir(parents=True, exist_ok=True)
    _save(img, display, 82)
    _save(img.thumbnail_image(THUMB_PX), thumb, 70)
    return f"{rel}.webp", f"{rel}_t.webp", img.width, img.height


def process_one() -> bool:
    """Convert the newest pending image. False when there is none."""
    s = get_settings()
    with get_engine().begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT id, path, satellite, sector, product, kind, overlay, scan_time FROM image_frame
                WHERE status = 'pending' ORDER BY scan_time DESC, id DESC LIMIT 1
                FOR UPDATE SKIP LOCKED
                """
            )
        ).mappings().first()
        if row is None:
            return False
        src = s.data_root / row["path"]
        upd = {"id": row["id"], "err": None, "display": None, "thumb": None, "w": None, "h": None}
        if not src.exists():
            upd["status"] = "gone"
        else:
            try:
                rel = media_rel(row["satellite"], row["sector"], row["scan_time"], row["product"], row["overlay"])
                upd["display"], upd["thumb"], upd["w"], upd["h"] = make_copies(src, s.media_root, rel)
                upd["status"] = "ok"
            except Exception as exc:
                log.exception("failed: %s", row["path"])
                upd["status"], upd["err"] = "error", f"{type(exc).__name__}: {exc}"[:500]
        conn.execute(
            text(
                """
                UPDATE image_frame SET status = :status, error = :err, display = :display, thumb = :thumb,
                       width = :w, height = :h, made_at = now() WHERE id = :id
                """
            ),
            upd,
        )
        if upd["status"] == "ok":
            notify(conn, {"type": "frame", "id": row["id"], "satellite": row["satellite"], "sector": row["sector"],
                          "product": row["product"], "kind": row["kind"], "overlay": row["overlay"],
                          "scan_time": row["scan_time"].isoformat(), "display": upd["display"],
                          "thumb": upd["thumb"], "width": upd["w"], "height": upd["h"]})
    return True


def main() -> None:
    ap = argparse.ArgumentParser(prog="satweather-imaging")
    ap.add_argument("--once", action="store_true", help="drain the pending queue, then exit")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # pyvips forwards libvips' per-operation chatter ("reduceh: 17 point
    # mask") at INFO; a few lines per image would drown the journal.
    logging.getLogger("pyvips").setLevel(logging.WARNING)
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    import pyvips

    # libvips' operation cache only helps repeated work on the same image;
    # here every image is new, and the cache would just hold memory.
    pyvips.cache_set_max(0)
    log.info("starting (libvips %s.%s)", pyvips.version(0), pyvips.version(1))
    done, t0 = 0, time.time()
    while not _stop:
        if process_one():
            done += 1
            if done % 50 == 0:
                log.info("%d images in %.0fs", done, time.time() - t0)
            continue
        if done:
            log.info("caught up: %d images in %.0fs", done, time.time() - t0)
            done, t0 = 0, time.time()
        if args.once:
            return
        for _ in range(int(IDLE_S * 10)):
            if _stop:
                break
            time.sleep(0.1)
    log.info("stopped")


if __name__ == "__main__":
    main()
