"""Text product -> structured data, via pyiem's NWS parsers.

pyiem was written for the Iowa Environmental Mesonet and, left to its
defaults, looks up UGC and station metadata in IEM's own database
(iemdb-*.local). Every call here passes explicit (empty) providers so it
never tries: names and geometry come from our reference tables at query
time instead.

Parsing never raises. A product pyiem cannot handle is still stored, raw
and searchable, with parse_error set.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from datetime import datetime

from satweather.catalog import describe
from satweather.emwin import EmwinName
from satweather.reports import extract as extract_reports

warnings.filterwarnings("ignore", module="pyiem")

# Imported lazily: pyiem pulls in pandas/geopandas/matplotlib, which the
# API process never needs.
_parsers = None


def _load():
    global _parsers
    if _parsers is None:
        from pyiem.nws.product import TextProduct
        from pyiem.nws.products import parser as text_parser
        from pyiem.nws.products.metarcollect import parser as metar_parser

        _parsers = (text_parser, metar_parser, TextProduct)
    return _parsers


METAR_NNN = {"SAH", "OBS", "MIS", "SSA"}


@dataclass
class Vtec:
    action: str
    office: str
    phenomena: str
    significance: str
    etn: int
    year: int
    begins: datetime | None
    ends: datetime | None


@dataclass
class Segment:
    seq: int
    ugcs: list[str]
    ugc_expire: datetime | None
    headlines: list[str]
    text: str
    polygon_wkt: str | None
    vtec: list[Vtec]
    motion: dict | None
    tags: dict


@dataclass
class Metar:
    station: str
    valid: datetime
    raw: str
    temp_c: float | None
    dewpoint_c: float | None
    wind_dir: float | None
    wind_kt: float | None
    gust_kt: float | None
    vis_mi: float | None
    altimeter_in: float | None
    kind: str


@dataclass
class Parsed:
    afos: str
    nnn: str
    office: str
    issued: datetime | None
    title: str
    category: str
    segments: list[Segment] = field(default_factory=list)
    metars: list[Metar] = field(default_factory=list)
    reports: list = field(default_factory=list)  # satweather.reports.Report (TAF, PIREP)
    error: str | None = None


def _title(text: str, fallback: str) -> str:
    """The product's own name line, e.g. 'Severe Thunderstorm Warning'.

    The first prose line after the WMO/AWIPS header that is not a UGC,
    VTEC or broadcast tag; products without one get the catalog name.
    """
    for line in text.split("\n")[:16]:
        s = line.strip()
        if not s or s.startswith("/") or s.endswith("-") or s.startswith("BULLETIN") or s.startswith("$$") or "BROADCAST REQUESTED" in s.upper():
            continue
        # A coded report is not a title ("TAF AMD", "METAR KSFO ...").
        if s.split()[0] in ("TAF", "METAR", "SPECI"):
            continue
        # Header tokens: "000", the WMO line, the AWIPS id.
        if " " not in s or (len(s.split()) == 3 and s.split()[2].isdigit()):
            continue
        s = re.sub(r"^URGENT\s*-\s*", "", s, flags=re.I)
        if s.isupper() and len(s) > 70:
            continue
        # Coded data (METAR, TEMP, SHEF, ...) is not a title: it has '=',
        # ':' leaders or few real words.
        words = re.findall(r"[A-Za-z]{3,}", s)
        digits = sum(c.isdigit() for c in s)
        if "=" in s or s.startswith(":") or len(words) < 2 or digits > len(s) * 0.25:
            continue
        return s[:120].title() if s.isupper() else s[:120]
    return fallback


def _vtec(v, issued: datetime | None) -> Vtec:
    ref = v.begints or issued
    return Vtec(
        action=v.action,
        office=v.office4 or v.office,
        phenomena=v.phenomena,
        significance=v.significance,
        etn=int(v.etn),
        year=v.year or (ref.year if ref else 0),
        begins=v.begints,
        ends=v.endts,
    )


def _tags(s) -> dict:
    out = {}
    for k in ("hailtag", "windtag", "windtagunits", "tornadotag", "damagetag", "hailthreat", "windthreat"):
        val = getattr(s, k, None)
        if val:
            out[k.replace("tag", "")] = val
    if getattr(s, "is_emergency", False):
        out["emergency"] = True
    if getattr(s, "is_pds", False):
        out["pds"] = True
    return out


def _metar(m) -> Metar:
    def val(q, unit):
        try:
            return round(q.value(unit), 2) if q is not None else None
        except Exception:
            return None

    return Metar(
        station=m.station_id,
        valid=m.time,
        raw=m.code,
        temp_c=val(m.temp, "C"),
        dewpoint_c=val(m.dewpt, "C"),
        wind_dir=val(m.wind_dir, "DEG"),
        wind_kt=val(m.wind_speed, "KT"),
        gust_kt=val(m.wind_gust, "KT"),
        vis_mi=val(m.vis, "MI"),
        altimeter_in=val(m.press, "IN"),
        kind=getattr(m, "type", "METAR") or "METAR",
    )


def parse(text: str, name: EmwinName, utcnow: datetime) -> Parsed:
    nnn = name.nnn
    display, category = describe(nnn)
    out = Parsed(afos=name.pil, nnn=nnn, office=name.cccc, issued=None, title=display, category=category)
    try:
        out.reports = extract_reports(nnn, text, name.received)
    except Exception:  # a decoder bug must not lose the product
        out.reports = []
    text_parser, metar_parser, generic = _load()
    try:
        if nnn in METAR_NNN:
            p = metar_parser(text, utcnow=utcnow, ugc_provider={}, nwsli_provider={})
            out.metars = [_metar(m) for m in p.metars]
        else:
            p = text_parser(text, utcnow=utcnow, ugc_provider={}, nwsli_provider={})
    except Exception as exc:
        # pyiem picks a specialist parser by NNN, and some NNNs cover more
        # than one product: every SWO goes to the MCD parser, so the day 1
        # outlook (SWODY1) fails on its missing LAT...LON, as does FFG
        # guidance that is not an MPD. The generic parser still gets the
        # segments, UGCs and VTEC.
        try:
            if nnn in METAR_NNN:
                raise
            p = generic(text, utcnow=utcnow, ugc_provider={}, nwsli_provider={})
        except Exception:
            out.error = f"{type(exc).__name__}: {str(exc)[:300]}"
            return out

    out.afos = p.afos or name.pil
    # pyiem's AFOS can differ from the filename's (HAW files carry TWOAT):
    # the text wins, it is what the office sent. Not for METAR
    # collectives, whose "AFOS" is just the word METAR or SPECI.
    if nnn not in METAR_NNN:
        out.nnn = out.afos[:3]
        out.title, out.category = describe(out.nnn)
    out.office = p.source or name.cccc
    out.issued = p.valid
    out.title = _title(p.unixtext, out.title)
    for i, s in enumerate(p.segments):
        motion = None
        if s.tml_giswkt:
            motion = {"wkt": s.tml_giswkt.split(";", 1)[-1], "dir": s.tml_dir, "kt": s.tml_sknt}
        out.segments.append(
            Segment(
                seq=i,
                ugcs=[str(u) for u in s.ugcs],
                ugc_expire=s.ugcexpire,
                headlines=list(s.headlines or []),
                text=s.unixtext,
                polygon_wkt=s.sbw.wkt if s.sbw is not None else None,
                vtec=[_vtec(v, p.valid) for v in s.vtec],
                motion=motion,
                tags=_tags(s),
            )
        )
    return out
