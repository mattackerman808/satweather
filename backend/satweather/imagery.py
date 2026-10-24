"""Images on the share: what each file is, and cataloguing them.

Satellite imagery. SatDump writes one directory per scan, named for its
UTC start time:

    IMAGES/GOES-18/Full Disk/2026-10-24_03-00-22/G18_13_20261024T030022Z.png
    IMAGES/GOES-18/Mesoscale 1/2026-10-24_03-07-26/abi_rgb_ABI_False_Color_map.png
    IMAGES/Himawari/2026-10-24_02-51-00/HIM_3_20261024T025100Z.png   (no sector level)

Two kinds of file: single bands (<SAT>_<band>_<time>Z.png) and RGB
composites (<instrument>_rgb_<Name>.png), each composite also drawn with
borders (<...>_map.png). A scan directory fills over several minutes
(Full Disk bands arrive one at a time), so files are catalogued one by
one as each settles, not per directory. product.cbor beside them holds
the geostationary projection, kept for reprojecting onto the map later.

L2/ (derived products: cloud height and temperature, precipitable water,
stability, rain rate, SST, LST) has the same layout as IMAGES/ and is
catalogued under sector "L2". Only the colorized composites are taken;
the raw grayscale product beside each adds nothing a viewer can read.

NWS graphics, all under source "NWS":
  - LRIT charts, IMAGES/NWS/<YYYYmmddHHMMSS>-<chart>_latest[BW].lrit.gif
    (TAFB/OPC Pacific analyses and forecasts), re-sent hourly whether or
    not they changed; satweather.imaging drops the byte-identical repeats.
  - EMWIN graphics, the non-text files in EMWIN/ (RADxxxxx regional radar
    mosaics, the national hazards map, SPC/WPC outlooks, ...), catalogued
    by satweather.ingest as each one is ingested; `from_emwin`.

Cataloguing only records the file (status pending); satweather.imaging
makes the web copies.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Connection, text

log = logging.getLogger("satweather.imagery")

_SCAN_DIR = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})$")
_BAND = re.compile(r"^[A-Z0-9]+_(?P<band>\d{1,2})_\d{8}T\d{6}Z\.png$")
_RGB = re.compile(r"^[a-z]+_rgb_(?P<name>.+?)(?P<map>_map)?\.png$")
_LRIT = re.compile(r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})-(?P<chart>.+?)(?:_latest(?:BW)?)?\.lrit\.gif$", re.I)
_TROPICAL = re.compile(r"^(AL|EP|CP)(\d{2})")

# The satellites SatDump writes under IMAGES/; NWS/ holds LRIT charts.
SATELLITES = ("GOES-18", "GOES-19", "Himawari")
NWS = "NWS"

# Radar mosaics by region (EMWIN RADxxxxx), in the order the picker shows.
RADAR = {
    "RADREFUS": "CONUS", "RADPACNW": "Pacific Northwest", "RADPACSW": "Pacific Southwest",
    "RADRCKNT": "Northern Rockies", "RADRCKST": "Southern Rockies", "RADNTHPL": "Northern Plains",
    "RADSTHPL": "Southern Plains", "RADUMSVY": "Upper Mississippi Valley",
    "RADSMSVY": "Southern Mississippi Valley", "RADGRTLK": "Great Lakes", "RADNTHES": "Northeast",
    "RADSTHES": "Southeast", "RADALLAK": "Alaska", "RADALLHI": "Hawaii", "RADALLPR": "Puerto Rico",
    "RADALLGU": "Guam",
}
CHARTS = {
    "IMGWWAUS": "US watches, warnings & advisories",
    "MODDY1US": "SPC day 1 convective outlook",
    "MOD94SUS": "WPC day 1 excessive rainfall outlook",
    "GPHJ88US": "Significant river flood outlook",
    "IMGFNT00": "North America surface analysis",
    "NPSA01US": "Pacific surface analysis (OPC)",
    "CSA001US": "Caribbean surface analysis (NHC)",
    # LRIT
    "pacsfc24": "Tropical E Pacific 24 h surface forecast",
    "pacsfc48": "Tropical E Pacific 48 h surface forecast",
    "pac24": "Tropical E Pacific 24 h wind/wave forecast",
    "pac48": "Tropical E Pacific 48 h wind/wave forecast",
    "pac48per": "Tropical E Pacific 48 h wave period forecast",
    "pacsea": "Tropical E Pacific sea state analysis",
    "WATL": "SW North Atlantic surface analysis",
}
_TROPICAL_KIND = {"WS": "surface wind field"}

_ABI = {
    1: "Blue 0.47 µm", 2: "Red 0.64 µm", 3: "Veggie 0.86 µm", 4: "Cirrus 1.37 µm", 5: "Snow/ice 1.6 µm",
    6: "Cloud particle size 2.2 µm", 7: "Shortwave IR 3.9 µm", 8: "Upper-level water vapor 6.2 µm",
    9: "Mid-level water vapor 6.9 µm", 10: "Lower-level water vapor 7.3 µm", 11: "Cloud-top phase 8.4 µm",
    12: "Ozone 9.6 µm", 13: "Clean longwave IR 10.3 µm", 14: "Longwave IR 11.2 µm",
    15: "Dirty longwave IR 12.3 µm", 16: "CO₂ longwave IR 13.3 µm",
}
_AHI = {
    1: "Blue 0.47 µm", 2: "Green 0.51 µm", 3: "Red 0.64 µm", 4: "Veggie 0.86 µm", 5: "Snow/ice 1.6 µm",
    6: "Cloud particle size 2.3 µm", 7: "Shortwave IR 3.9 µm", 8: "Upper-level water vapor 6.2 µm",
    9: "Mid-level water vapor 6.9 µm", 10: "Lower-level water vapor 7.3 µm", 11: "Cloud-top phase 8.6 µm",
    12: "Ozone 9.6 µm", 13: "Clean longwave IR 10.4 µm", 14: "Longwave IR 11.2 µm",
    15: "Dirty longwave IR 12.4 µm", 16: "CO₂ longwave IR 13.3 µm",
}


@dataclass(frozen=True)
class ImageName:
    source: str
    sector: str
    scan_time: datetime
    product: str  # band ("13"), composite as in the file ("ABI_False_Color"), or chart id ("RADREFUS")
    kind: str  # band | rgb | graphic
    overlay: bool


def parse_path(rel: str) -> ImageName | None:
    """IMAGES/<sat>[/<sector>]/<scan dir>/<file>, L2/<sat>/<sector>/<scan
    dir>/<file> or IMAGES/NWS/<chart> -> what it is, or None."""
    parts = rel.split("/")
    if len(parts) == 3 and parts[:2] == ["IMAGES", NWS] and (m := _LRIT.match(parts[2])):
        t = datetime(*(int(x) for x in m.groups()[:6]), tzinfo=UTC)
        return ImageName(NWS, "Charts", t, m["chart"], "graphic", False)
    if len(parts) not in (4, 5) or parts[0] not in ("IMAGES", "L2") or parts[1] not in SATELLITES:
        return None
    satellite, scan_dir, name = parts[1], parts[-2], parts[-1]
    sector = "L2" if parts[0] == "L2" else parts[2] if len(parts) == 5 else "Full Disk"
    m = _SCAN_DIR.match(scan_dir)
    if not m:
        return None
    scan_time = datetime(*(int(x) for x in m.groups()), tzinfo=UTC)
    if sector != "L2" and (b := _BAND.match(name)):
        return ImageName(satellite, sector, scan_time, str(int(b["band"])), "band", False)
    if r := _RGB.match(name):
        return ImageName(satellite, sector, scan_time, r["name"], "rgb", bool(r["map"]))
    return None


def scan_dir_time(name: str) -> datetime | None:
    """UTC start of the scan a SatDump directory holds, from its name."""
    m = _SCAN_DIR.match(name)
    return datetime(*(int(x) for x in m.groups()), tzinfo=UTC) if m else None


def lrit_time(name: str) -> datetime | None:
    """When an LRIT chart (IMAGES/NWS/) was received, from its name."""
    m = _LRIT.match(name)
    return datetime(*(int(x) for x in m.groups()[:6]), tzinfo=UTC) if m else None


def from_emwin(awips: str, received: datetime) -> ImageName:
    """An EMWIN graphic (awips as in the filename, e.g. RADREFUS)."""
    return ImageName(NWS, "Radar" if awips.startswith("RAD") else "Charts", received, awips, "graphic", False)


def label(source: str, product: str, kind: str) -> str:
    """'Band 13 · Clean longwave IR 10.3 µm', 'ABI False Color', 'Pacific Southwest'."""
    if kind == "graphic":
        if name := RADAR.get(product) or CHARTS.get(product):
            return name
        if m := _TROPICAL.match(product):
            what = _TROPICAL_KIND.get(product[-2:], product[4:])
            return f"{m[1]}{m[2]} {what}"
        return product
    if kind == "band":
        names = _AHI if source == "Himawari" else _ABI
        n = int(product)
        return f"Band {n} · {names[n]}" if n in names else f"Band {n}"
    return product.replace("_", " ")


def read_projection(scan_dir: Path) -> dict | None:
    """product.cbor's projection_cfg, or None if absent or not readable yet."""
    try:
        import cbor2

        with open(scan_dir / "product.cbor", "rb") as f:
            cfg = cbor2.load(f).get("projection_cfg")
        return cfg if isinstance(cfg, dict) else None
    except Exception:
        return None


def load_seen(conn: Connection, days: int) -> set[str]:
    rows = conn.execute(
        text("SELECT path FROM image_frame WHERE catalogued_at > now() - make_interval(days => :d)"), {"d": days}
    )
    return {r[0] for r in rows}


class Scanner:
    """Walks IMAGES/ and L2/ each pass (satellite scan directories and
    NWS/). A directory whose mtime has not moved since a pass that found
    nothing unsettled in it is skipped without listing: NFS readdirs are
    the cost here, and finished scans are most of the tree."""

    def __init__(self, root: Path, seen: set[str]):
        self.root = root
        self.seen = seen
        self._done: dict[str, float] = {}

    def _scan_dirs(self):
        try:
            yield from (e for e in os.scandir(self.root / "IMAGES") if e.name == NWS and e.is_dir())
        except OSError:
            pass
        for top, sat in ((t, s) for t in ("IMAGES", "L2") for s in SATELLITES):
            base = self.root / top / sat
            try:
                subs = [e for e in os.scandir(base) if e.is_dir(follow_symlinks=False)]
            except OSError:
                continue
            for e in subs:
                if _SCAN_DIR.match(e.name):
                    yield e
                    continue
                try:
                    yield from (s for s in os.scandir(e.path)
                                if s.is_dir(follow_symlinks=False) and _SCAN_DIR.match(s.name))
                except OSError:
                    continue

    def scan(self, conn_factory, settle_s: float, stop=lambda: False) -> int:
        """Catalogue every settled, unseen image. Returns how many."""
        n = 0
        now = time.time()
        live: set[str] = set()
        for d in self._scan_dirs():
            if stop():
                break
            live.add(d.path)
            try:
                mtime = d.stat(follow_symlinks=False).st_mtime
            except OSError:
                continue
            if self._done.get(d.path) == mtime:
                continue
            new, unsettled = [], False
            try:
                entries = list(os.scandir(d.path))
            except OSError:
                continue
            for f in entries:
                if not f.name.endswith((".png", ".gif")):
                    continue
                rel = os.path.relpath(f.path, self.root)
                if rel in self.seen:
                    continue
                try:
                    st = f.stat(follow_symlinks=False)
                except OSError:
                    continue
                if st.st_size == 0 or now - st.st_mtime < settle_s:
                    unsettled = True
                    continue
                new.append((rel, st))
            if new:
                proj = None if d.name == NWS else read_projection(Path(d.path))
                with conn_factory() as conn:
                    for rel, st in new:
                        if (im := parse_path(rel)) is not None:
                            store_image(conn, rel, im, st.st_size, st.st_mtime, proj)
                            n += 1
                        self.seen.add(rel)
            if not unsettled:
                self._done[d.path] = mtime
        # Forget directories the share has pruned.
        self._done = {k: v for k, v in self._done.items() if k in live}
        return n


def store_image(conn: Connection, rel: str, im: ImageName, size: int, mtime: float,
                projection: dict | None = None) -> bool:
    """True if the image is new (not already catalogued)."""
    return conn.execute(
        text(
            """
            INSERT INTO image_frame (path, source, sector, product, kind, overlay, scan_time, size, mtime, projection)
            VALUES (:path, :src, :sector, :product, :kind, :overlay, :t, :size, to_timestamp(:mtime),
                    CAST(:proj AS jsonb))
            ON CONFLICT (path) DO NOTHING
            """
        ),
        {"path": rel, "src": im.source, "sector": im.sector, "product": im.product, "kind": im.kind,
         "overlay": im.overlay, "t": im.scan_time, "size": size, "mtime": mtime,
         "proj": json.dumps(projection) if projection else None},
    ).rowcount == 1
