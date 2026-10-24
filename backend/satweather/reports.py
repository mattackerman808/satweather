"""Individual METAR / TAF / PIREP reports inside an EMWIN product.

Collectives hold many reports, each terminated by '='. They are split
here and each one is run through satweather.wxdecode, the decoder
vendored from Tachbase, for the group-by-group meanings, ceiling and
flight category that the map and the detail panels show.

pyiem still extracts METARs for their numeric fields (temperature, wind,
altimeter); this module adds the decoded form. TAFs and PIREPs come only
from here: pyiem's TAF parser rejects the international formats EMWIN
carries, and it has no PIREP parser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from satweather import wxdecode

TAF_NNN = {"TAF"}
PIREP_NNN = {"UAB", "PRC", "PIR"}

_DDHHMMZ = re.compile(r"\b(\d{2})(\d{2})(\d{2})Z\b")
# A TB or IC group is a hazard only if it reports some: "TB NEG", "TB SMTH"
# and "IC NEGCLR" are pilots saying there was none.
# Pilots and controllers abbreviate freely ("LGT", "LT", "LIGHT", "MDT").
_INTENSITY = {
    "TB": re.compile(r"\b(?:LGT|LT|LIGHT|MOD|MDT|MODERATE|SEV|SEVERE|EXTRM|EXTREME)\b"),
    "IC": re.compile(r"\b(?:TRACE|TRC|LGT|LT|LIGHT|MOD|MDT|MODERATE|SEV|SEVERE)\b"),
}
_VALID = re.compile(r"\b(\d{2})(\d{2})/(\d{2})(\d{2})\b")
_PIREP_START = re.compile(r"\b([A-Z0-9]{3,4}\s+UU?A\b)")
_TM = re.compile(r"/TM\s*(\d{2})(\d{2})")


@dataclass
class Report:
    kind: str  # METAR | SPECI | TAF | PIREP
    station: str | None
    raw: str
    decoded: dict
    flight_category: str | None = None
    issued: datetime | None = None  # METAR observed / TAF issued / PIREP time
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    urgent: bool = False
    hazards: list[str] = field(default_factory=list)


def _body(text: str) -> str:
    """Product text without the WMO heading and AWIPS lines."""
    lines = text.split("\n")
    return "\n".join(lines[2:])


def chunks(text: str) -> list[str]:
    """'='-terminated reports, whitespace collapsed."""
    out = []
    for c in _body(text).split("="):
        c = " ".join(c.split())
        if c:
            out.append(c)
    return out


def ddhh(day: int, hour: int, minute: int, ref: datetime) -> datetime | None:
    """A day-of-month time near `ref`, which may fall in the previous or
    next month (a TAF issued on the 31st valid until the 1st)."""
    best = None
    for months in (-1, 0, 1):
        y, m = ref.year, ref.month + months
        if m == 0:
            y, m = y - 1, 12
        elif m == 13:
            y, m = y + 1, 1
        try:
            base = datetime(y, m, day, tzinfo=UTC)
        except ValueError:
            continue
        cand = base + timedelta(hours=hour, minutes=minute)  # hour 24 rolls over
        if best is None or abs(cand - ref) < abs(best - ref):
            best = cand
    return best


def _taf(chunk: str, ref: datetime) -> Report | None:
    text = chunk if chunk.startswith("TAF") else f"TAF {chunk}"
    if not _VALID.search(text):
        return None
    d = wxdecode.decode_taf(" ".join(text.replace("TAF TAF ", "TAF ").split()))
    if d.get("error"):
        return None
    issued = vf = vt = None
    if (m := _DDHHMMZ.search(text)):
        issued = ddhh(int(m[1]), int(m[2]), int(m[3]), ref)
    if (v := _VALID.search(text)):
        vf = ddhh(int(v[1]), int(v[2]), 0, issued or ref)
        vt = ddhh(int(v[3]), int(v[4]), 0, vf or ref)
    first = (d.get("periods") or [{}])[0].get("summary", {})
    return Report("TAF", d.get("station"), text, d, first.get("flight_category"), issued, vf, vt)


def _pirep(chunk: str, ref: datetime) -> Report | None:
    m = _PIREP_START.search(chunk)
    if not m:
        return None
    text = chunk[m.start():]
    d = wxdecode.decode(text)
    if d.get("type") != "PIREP":
        return None
    when = None
    if (t := _TM.search(text)):
        cand = ref.replace(hour=int(t[1]), minute=int(t[2]), second=0, microsecond=0)
        when = cand - timedelta(days=1) if cand > ref + timedelta(hours=1) else cand
    hazards = sorted({k for g in d.get("decoded", [])
                      if (k := g["group"].split()[0]) in ("TB", "IC", "WS", "WX")
                      and (k not in _INTENSITY or _INTENSITY[k].search(g["group"]))})
    return Report("PIREP", d.get("station"), text, d, None, when, urgent=" UUA" in f" {text}", hazards=hazards)


def metar(raw: str) -> tuple[dict, str | None]:
    """Decoded METAR and its flight category, from the raw report."""
    d = wxdecode.decode(raw if raw.startswith(("METAR", "SPECI")) else f"METAR {raw}")
    return d, d.get("summary", {}).get("flight_category")


def extract(nnn: str, text: str, received: datetime) -> list[Report]:
    """TAFs and PIREPs in a product (METARs are taken from pyiem)."""
    out: list[Report] = []
    if nnn in TAF_NNN:
        for c in chunks(text):
            if (r := _taf(c, received)):
                out.append(r)
    elif nnn in PIREP_NNN or " UA /OV" in text or " UUA /OV" in text:
        for c in chunks(text):
            if (r := _pirep(c, received)):
                out.append(r)
    return out
