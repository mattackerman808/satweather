"""CPC's 6-10 and 8-14 day outlook tables (the end of PMDMRD), read for a place.

    6-10 DAY OUTLOOK TABLE
    OUTLOOK FOR NOV 09 - 13 2026
    STATE         TEMP PCPN    STATE         TEMP PCPN ...
    WASHINGTON     A    A      OREGON         A    A ...
    ...
    LEGEND
    TEMPS WITH RESPECT TO NORMAL  ... A - ABOVE  N - NEAR NORMAL  B - BELOW

Each state (and a few sub-state regions: east/west Montana, three parts
of Texas, north/south California, Florida's panhandle and peninsula,
seven Alaska regions) gets A/N/B for temperature and precipitation. The
Forecast tab's days 8-10 come from here, not from the LLM.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

_MONTHS = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}
_TABLE = re.compile(r"(6-10|8-14) DAY OUTLOOK TABLE\s+OUTLOOK FOR\s+([A-Z]{3})\s+(\d{1,2})\s*-\s*(?:([A-Z]{3})\s+)?(\d{1,2})\s+(\d{4})",
                    re.I)
_ROW = re.compile(r"([A-Z][A-Z .]*?[A-Z])\s+([ANB])\s+([ANB])(?=\s|$)")
TILT = {"A": "above", "N": "near", "B": "below"}

_STATES = {
    "WA": "WASHINGTON", "OR": "OREGON", "ID": "IDAHO", "NV": "NEVADA", "WY": "WYOMING", "UT": "UTAH", "AZ": "ARIZONA",
    "CO": "COLORADO", "NM": "NEW MEXICO", "ND": "N DAKOTA", "SD": "S DAKOTA", "NE": "NEBRASKA", "KS": "KANSAS",
    "OK": "OKLAHOMA", "MN": "MINNESOTA", "IA": "IOWA", "MO": "MISSOURI", "AR": "ARKANSAS", "LA": "LOUISIANA",
    "WI": "WISCONSIN", "IL": "ILLINOIS", "MS": "MISSISSIPPI", "MI": "MICHIGAN", "IN": "INDIANA", "OH": "OHIO",
    "KY": "KENTUCKY", "TN": "TENNESSEE", "AL": "ALABAMA", "NY": "NEW YORK", "VT": "VERMONT", "NH": "NEW HAMP",
    "ME": "MAINE", "MA": "MASS", "CT": "CONN", "RI": "RHODE IS", "PA": "PENN", "NJ": "NEW JERSEY",
    "WV": "W VIRGINIA", "MD": "MARYLAND", "DE": "DELAWARE", "VA": "VIRGINIA", "NC": "N CAROLINA",
    "SC": "S CAROLINA", "GA": "GEORGIA", "DC": "MARYLAND",
}


def region(state: str | None, lon: float, lat: float) -> str | None:
    """The table row for a place: its state, or the part of it CPC splits out."""
    if not state:
        return None
    s = state.upper()
    if s == "MT":
        return "W MONTANA" if lon < -109.5 else "E MONTANA"
    if s == "TX":
        if lon < -101.5:
            return "W TEXAS"
        return "N TEXAS" if lat > 31.5 else "S TEXAS"
    if s == "CA":
        return "NRN CALIF" if lat > 36.0 else "SRN CALIF"
    if s == "FL":
        return "FL PNHDL" if lon < -84.0 and lat > 29.5 else "FL PENIN"
    if s == "AK":
        if lon < -164 and lat < 56.5:
            return "AK ALEUTIAN"
        if lat > 68:
            return "AK N SLOPE"
        if lon > -141.5 and lat < 60.5:
            return "AK PNHDL"
        if lon < -157:
            return "AK WESTERN"
        if lat < 61.5:
            return "AK SO COAST"
        return "AK INT BSN" if lat > 63 else "AK S INT"
    return _STATES.get(s)


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().upper())


def parse_tables(raw: str) -> dict[str, dict]:
    """{"6-10" | "8-14": {"start": date, "end": date, "rows": {name: (temp, pcpn)}}}."""
    text = raw.replace("\r", "")
    out: dict[str, dict] = {}
    ms = list(_TABLE.finditer(text))
    for i, m in enumerate(ms):
        try:
            y = int(m[6])
            m1 = _MONTHS[m[2].upper()]
            m2 = _MONTHS[(m[4] or m[2]).upper()]
            end = date(y, m2, int(m[5]))
            start = date(y if m1 <= m2 else y - 1, m1, int(m[3]))
        except (KeyError, ValueError):
            continue
        body_end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        body = text[m.end():body_end]
        body = body[: body.upper().find("LEGEND")] if "LEGEND" in body.upper() else body
        body = re.sub(r"STATE\s+TEMP\s+PCPN", " ", body, flags=re.I)
        rows: dict[str, tuple[str, str]] = {}
        for line in body.split("\n"):
            for r in _ROW.finditer(line.upper()):
                rows[_norm(r[1])] = (r[2], r[3])
        out[m[1]] = {"start": start, "end": end, "rows": rows}
    return out


def _row(rows: dict, name: str) -> tuple[str, str] | None:
    if name in rows:
        return rows[name]
    # A row whose name ran into its neighbour's column (`NEW YORKA B`).
    squeezed = name.replace(" ", "")
    return next((v for k, v in rows.items() if k.replace(" ", "") == squeezed), None)


def later(raw: str | None, reg: str | None, dates: list[str]) -> list[dict]:
    """[{date, temp, precip, period}] for each date one of the tables covers
    (the 6-10 day table first), for the place's row."""
    if not raw or not reg or not dates:
        return []
    tables = parse_tables(raw)
    out = []
    for d in dates:
        day = date.fromisoformat(d)
        for period in ("6-10", "8-14"):
            t = tables.get(period)
            if not t or not (t["start"] <= day <= t["end"]):
                continue
            v = _row(t["rows"], reg)
            if v:
                out.append({"date": d, "temp": TILT[v[0]], "precip": TILT[v[1]], "period": period,
                            "region": reg.title()})
                break
    return out


def later_dates(last_pfm_day: str | None, today: date) -> list[str]:
    """Days 8-10 counting today as day 1, past the PFM's last day."""
    last = date.fromisoformat(last_pfm_day) if last_pfm_day else today
    return [(today + timedelta(days=n)).isoformat() for n in (7, 8, 9) if today + timedelta(days=n) > last]
