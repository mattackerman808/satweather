"""NWS Point Forecast Matrices (PFM) parsed into numbers, plus the
day/night summaries and plain-language conditions the Forecast tab shows.

A PFM has one segment per forecast point:

    ILZ104-042200-
    Chicago O'Hare International Airport-Cook IL
    41.99N  87.91W Elev. 667 ft
    1215 AM CST Wed Nov 4 2026

    Date             Wed 11/04/26            Thu 11/05/26 ...
    CST 3hrly     03 06 09 12 15 18 21 00 03 06 09 12 15 ...
    UTC 3hrly     09 12 15 18 21 00 03 06 09 12 15 18 21 ...

    Max/Min                      64          49          57 ...
    Temp          64 63 62 61 58 56 54 53 51 50 55 57 57 ...

then a second, 6-hourly block out to day 7. Values sit in columns under
the hour headers. Read them BY COLUMN, not by splitting on whitespace:
three-digit values touch (`RH 96100100`), blank cells are spaces, and
offices differ in label case and spacing (`Max/Min`, `MIN/MAX`,
`CST3hrly`). The UTC row gives the times; Guam labels its LOCAL row
"GMT", so the row that starts with UTC wins. Some rows arrive with the
spaces between label and values squeezed out (`Dewpt47 49   47 43`): the
spacing between values survives, so such a row is shifted back so its
first value sits in its column.

12-hour rows (Max/Min, PoP, QPF, Snow) put each value at the END of the
period it covers: the high at ~18 local, the low at ~06. Whether a value
is a high or a low follows from that local hour, whatever order the
office's label names them in.

Nothing here touches the database, and nothing raises on odd input: a
segment or row that can't be read is skipped.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta

_UGC = re.compile(r"^([A-Z]{2}[CZ]\d{3}[-0-9A-Z>]*?)-(\d{6})-\s*$")
_COORDS = re.compile(r"(\d{1,2}\.\d+)\s*([NS])\s+(\d{1,3}\.\d+)\s*([EW])(?:\s+Elev\.\s*(-?\d+)\s*ft)?", re.I)
_FIRST_DATE = re.compile(r"(?:Date|DATE)\b.*?\b(\d{1,2})/(\d{1,2})(?:/(\d{2}))?")
# A Date row glued onto the end of another row: bytes lost in reception.
_GLUED_DATE = re.compile(r"(?<=\S)(?:Date|DATE)\s{2,}")
_DATE_ROW = re.compile(r"^date\b", re.I)
_HOUR_ROW = re.compile(r"^(?P<tz>[A-Za-z]{1,5})\s?(?P<step>[36])hrly", re.I)
_TOKEN = re.compile(r"\S+")

# Row label (lowercase, spaces removed) -> field name. Longest match wins.
_HOURLY = {
    "temp": "temp", "dewpt": "dewpt", "rh": "rh",
    "winddir": "wdir", "windspd": "wspd", "windgust": "wgust",
    "pwinddir": "wdir", "windchar": "wchar",
    "clouds": "clouds", "avgclouds": "clouds", "clouds(%)": "clouds_pct",
    "windchill": "chill", "heatindex": "heat",
}
_WX = {
    "rain": "rain", "rainshwrs": "showers", "tstms": "tstms", "drizzle": "drizzle", "sprinkles": "drizzle",
    "snow": "snow", "snowshwrs": "snow_showers", "flurries": "snow_showers",
    "frzgrain": "frzg_rain", "frzgdrzl": "frzg_rain", "sleet": "sleet", "obvis": "obvis",
}
_TWELVE = {"max/min": "maxmin", "min/max": "maxmin", "pop12hr": "pop", "qpf12hr": "qpf", "snow12hr": "snow"}
_ALL = {**{k: ("h", v) for k, v in _HOURLY.items()}, **{k: ("w", v) for k, v in _WX.items()},
        **{k: ("t", v) for k, v in _TWELVE.items()}}
_LABELS = sorted(_ALL, key=len, reverse=True)

# Likelihood codes on the weather rows, as a 1..4 rank.
_LIKELY = {"S": 1, "IS": 1, "C": 2, "SC": 2, "PA": 2, "AR": 2, "L": 3, "NM": 3, "O": 4, "WD": 4, "D": 4, "DF": 4}
_FOG = {"F", "PF", "AF", "DF", "FG", "+F"}
_SKY_RANK = {"CL": 0, "FW": 1, "SC": 2, "B1": 3, "B2": 4, "OV": 5}
# Wind character in the 6-hourly block -> a representative mph.
_WCHAR = {"LT": 5, "GN": 11, "BZ": 18, "WY": 26, "VW": 35, "SD": 45, "HF": 74}


@dataclass
class PfmPoint:
    ugc: str
    name: str
    lat: float
    lon: float
    elev_ft: int | None
    expires: datetime | None
    utc_offset_h: int
    series: list[dict] = field(default_factory=list)  # one per time: {"t": datetime, "step": 3|6, ...}
    twelve: list[dict] = field(default_factory=list)  # one per 12-h period end: {"t", "max"|"min", "pop", ...}
    extra: dict = field(default_factory=dict)  # unrecognised rows, raw

    def as_row(self) -> dict:
        return asdict(self)


def _num(s: str) -> float | None:
    try:
        return float(s)
    except ValueError:
        return None


def _key(s: str) -> str:
    return re.sub(r"\s+", "", s.lower())


def _match_label(line: str) -> tuple[str, int] | None:
    """(label key, index in `line` just after the label), or None."""
    k = _key(line)
    for lab in _LABELS:
        if k.startswith(lab):
            n, i = 0, 0
            while n < len(lab) and i < len(line):
                if not line[i].isspace():
                    n += 1
                i += 1
            # `Snow` must not swallow `Snow 12hr` / `Snowshwrs`: the longest label won already.
            return lab, i
    return None


def _expires(ugc_line_ddhhmm: str, issued: datetime) -> datetime | None:
    try:
        d, h, m = int(ugc_line_ddhhmm[:2]), int(ugc_line_ddhhmm[2:4]), int(ugc_line_ddhhmm[4:])
        t = issued.replace(day=1, hour=h, minute=m, second=0, microsecond=0)
        for months in (0, 1):
            y, mo = t.year + (t.month - 1 + months) // 12, (t.month - 1 + months) % 12 + 1
            try:
                c = t.replace(year=y, month=mo, day=d)
            except ValueError:
                continue
            if c >= issued - timedelta(hours=12):
                return c
    except (ValueError, IndexError):
        pass
    return None


def _walk(hours: list[int], start: datetime, after: bool) -> list[datetime]:
    """Absolute UTC times for a run of UTC hours. The first is the one nearest
    `start` (the first block, which can begin a little before issuance) or,
    with `after`, the first at or after it (later blocks)."""
    out: list[datetime] = []
    for h in hours:
        if not out:
            base = start.replace(hour=h, minute=0, second=0, microsecond=0)
            if after:
                t = base if base >= start else base + timedelta(days=1)
            else:
                t = min((base + timedelta(days=k) for k in (-1, 0, 1)), key=lambda c: abs(c - start))
        else:
            t = out[-1] + timedelta(hours=1)
            while t.hour != h:
                t += timedelta(hours=1)
        out.append(t)
    return out


def _offset(local_h: int, utc_h: int) -> int:
    d = (local_h - utc_h) % 24
    return d - 24 if d > 12 else d  # -12..+12: HST is -10, ChST +10


def _hour_cols(row: str | None) -> tuple[int, list[tuple[int, int]]] | None:
    """(step, [(column end, hour)]) for an hour-header row, or None when the
    row is damaged: every cell must be a two-digit hour, a step apart."""
    if row is None:
        return None
    m = _HOUR_ROW.match(row)
    if not m:
        return None
    step = int(m.group("step"))
    cols = [(t.end(), int(t.group())) for t in _TOKEN.finditer(row, m.end())]
    toks = [t.group() for t in _TOKEN.finditer(row, m.end())]
    if len(cols) < 2 or any(len(t) != 2 or not t.isdigit() for t in toks) or any(not 0 <= h <= 23 for _, h in cols):
        return None
    if any((b - a) % 24 != step for (_, a), (_, b) in zip(cols, cols[1:])):
        return None
    return step, cols


def _split_rows(hrows: list[str]) -> tuple[str | None, str | None]:
    """(UTC row, local row). The row labelled UTC wins over GMT: Guam labels
    its local row GMT."""
    utc = next((r for r in hrows if r.upper().startswith("UTC")), None) or \
        next((r for r in hrows if r.upper().startswith("GMT")), None)
    return utc, next((r for r in hrows if r is not utc), None)


def product_offset(raw: str) -> int | None:
    """The product's UTC offset from the first undamaged pair of hour rows."""
    lines = [ln for ln in raw.split("\n") if ln.strip()]
    for k, ln in enumerate(lines):
        if _HOUR_ROW.match(ln) and k + 1 < len(lines) and _HOUR_ROW.match(lines[k + 1]):
            u, loc = _split_rows([ln, lines[k + 1]])
            uc, lc = _hour_cols(u), _hour_cols(loc)
            if uc and lc and len(uc[1]) == len(lc[1]):
                return _offset(lc[1][0][1], uc[1][0][1])
    return None


def _anchor(date_line: str | None, issued: datetime) -> date | None:
    """The local date of a block's first column: the first date in its Date row."""
    m = _FIRST_DATE.search(date_line or "")
    if not m:
        return None
    mo, d = int(m.group(1)), int(m.group(2))
    years = [2000 + int(m.group(3))] if m.group(3) else [issued.year - 1, issued.year, issued.year + 1]
    best = None
    for y in years:
        try:
            c = date(y, mo, d)
        except ValueError:
            continue
        if best is None or abs((c - issued.date()).days) < abs((best - issued.date()).days):
            best = c
    if best is None or abs((best - issued.date()).days) > 9:
        return None
    return best


def _parse_block(lines: list[str], start: datetime, pt: PfmPoint, after: bool,
                 offset_hint: int | None, first_day: date | None = None) -> datetime | None:
    """One hour-header block into `pt`. Returns its last time.

    Columns come from the UTC row; when reception damaged it (`0306`), from
    the local row converted with the product's offset. The first column's
    time comes from the block's Date row (`first_day`, local) when there is
    one; otherwise it's walked from `start`."""
    hrows = [ln for ln in lines[:3] if _HOUR_ROW.match(ln)]
    utc_row, local_row = _split_rows(hrows)
    uc, lc = _hour_cols(utc_row), _hour_cols(local_row)
    if uc and lc and len(uc[1]) == len(lc[1]):
        pt.utc_offset_h = _offset(lc[1][0][1], uc[1][0][1])
    elif offset_hint is not None:
        pt.utc_offset_h = offset_hint
    if uc and lc and len(uc[1]) == len(lc[1]) and lc[1][0][0] > uc[1][0][0]:
        # The UTC row lost characters (`UTC 3hrly21 00 03`): lost bytes only
        # ever shift text left, so the local row has the true positions.
        step, cols = uc[0], [(le, h) for (le, _), (_, h) in zip(lc[1], uc[1])]
    elif uc:
        step, cols = uc
    elif lc and offset_hint is not None:
        step, cols = lc[0], [(e, (h - offset_hint) % 24) for e, h in lc[1]]
    else:
        return None
    if first_day is not None:
        local_h = (cols[0][1] + pt.utc_offset_h) % 24
        exact = datetime(first_day.year, first_day.month, first_day.day, local_h, tzinfo=UTC) \
            - timedelta(hours=pt.utc_offset_h)
        if abs(exact - start) <= timedelta(days=4) or step == 6:
            start, after = exact, True
    elif step == 6 and not after:
        # The 3-hourly block before it was lost in reception. The 6-hourly
        # one follows 22 three-hour columns that start up to ~12 h before
        # issuance, so it begins ~2.5 days after issuance.
        start = start + timedelta(hours=60)
    times = _walk([h for _, h in cols], start, after)
    ends = [e for e, _ in cols]

    rows: dict[str, list[str | None]] = {}
    twelve: dict[str, list[tuple[int, str]]] = {}
    slots12: list[int] = []  # 12-hour column ends, learnt from an unsqueezed 12-h row
    squeezed12: list[tuple[str, list[str]]] = []
    for ln in lines:
        if not ln.strip() or _HOUR_ROW.match(ln) or _DATE_ROW.match(ln) or "=" in ln:
            continue
        g = _GLUED_DATE.search(ln)
        if g:
            ln = ln[:g.start()]
        m = _match_label(ln)
        if m is None:
            label = ln[: max(ends[0] - 3, 1)].strip()
            if label:
                pt.extra.setdefault(label, []).append(ln[ends[0] - 3:].rstrip())
            continue
        lab, at = m
        kind, name = _ALL[lab]
        squeezed = at < len(ln) and not ln[at].isspace() and at < ends[0] - 3
        if kind == "t":
            toks = [(t.end(), t.group()) for t in _TOKEN.finditer(ln, at)]
            if squeezed:
                squeezed12.append((name, [v for _, v in toks]))
                continue
            twelve[name] = toks
            if not slots12:
                slots12 = [e for e, _ in toks]
            continue
        if squeezed:
            first = _TOKEN.search(ln, at)
            if first is None:
                continue
            # Put the first value back into its first-column slot.
            ln = ln[:at] + " " * (ends[0] - first.end()) + ln[at:]
        vals = [ln[e - 3:e].strip() or None for e in ends]
        rows[(kind, name)] = vals  # type: ignore[index]

    for name, vals in squeezed12:
        if slots12 and len(vals) <= len(slots12):
            twelve[name] = list(zip(slots12, vals))

    # Hourly series
    for i, t in enumerate(times):
        e: dict = {"t": t, "step": step}
        wx: dict[str, str] = {}
        for (kind, name), vals in rows.items():
            v = vals[i]
            if v is None:
                continue
            if kind == "w":
                wx[name] = v
            elif name in ("temp", "dewpt", "rh", "wspd", "wgust", "chill", "heat"):
                n = _num(v)
                if n is not None:
                    e[name] = int(n)
            elif name == "clouds_pct":
                n = _num(v)
                if n is not None:
                    e["clouds"] = _pct_sky(n)
            elif name == "clouds":
                if v.upper() in _SKY_RANK:
                    e["clouds"] = v.upper()
                elif (n := _num(v)) is not None:
                    e["clouds"] = _pct_sky(n)
            else:
                e[name] = v.upper()
        if wx:
            e["wx"] = wx
        if len(e) > 2:
            pt.series.append(e)

    # 12-hour periods, keyed by their end column
    by_end: dict[int, dict] = {}
    for name, toks in twelve.items():
        for end, v in toks:
            col = min(range(len(ends)), key=lambda j: abs(ends[j] - end))
            if abs(ends[col] - end) > 2:
                continue
            p = by_end.setdefault(col, {"t": times[col]})
            if name == "maxmin":
                n = _num(v)
                if n is not None:
                    local_h = (times[col] + timedelta(hours=pt.utc_offset_h)).hour
                    p["max" if 10 <= local_h <= 23 else "min"] = int(n)
            elif name == "pop":
                n = _num(v)
                if n is not None:
                    p["pop"] = int(n)
            elif name == "qpf":
                n = _num(v)
                if n is not None:
                    p["qpf"] = n
            elif name == "snow":
                p["snow"] = v
    pt.twelve.extend(by_end[c] for c in sorted(by_end))
    return times[-1]


def _pct_sky(pct: float) -> str:
    for limit, cat in ((6, "CL"), (25, "FW"), (50, "SC"), (69, "B1"), (87, "B2")):
        if pct < limit:
            return cat
    return "OV"


def parse_pfm(raw: str, issued: datetime) -> list[PfmPoint]:
    """Every point in a PFM product. `issued` is the product's UTC issue time."""
    out: list[PfmPoint] = []
    raw = raw.replace("\r", "")
    hint = product_offset(raw)
    for seg in raw.split("$$"):
        try:
            pt = _parse_segment(seg.split("\n"), issued, hint)
        except Exception:  # noqa: BLE001 — parsing never raises; a bad segment is skipped
            pt = None
        if pt is not None and pt.series:
            out.append(pt)
    return out


def _parse_segment(lines: list[str], issued: datetime, hint: int | None) -> PfmPoint | None:
    i = 0
    while i < len(lines) and not _UGC.match(lines[i].strip()):
        i += 1
    if i >= len(lines):
        return None
    um = _UGC.match(lines[i].strip())
    ugc = um.group(1)[:6]
    name, coords = None, None
    for j in range(i + 1, min(i + 10, len(lines))):
        c = _COORDS.search(lines[j])
        if c:
            coords = c
            named = [ln.strip() for ln in lines[i + 1:j] if ln.strip()]
            name = named[-1] if named else None
            i = j + 1
            break
    if coords is None:
        return None
    lat = float(coords.group(1)) * (-1 if coords.group(2).upper() == "S" else 1)
    lon = float(coords.group(3)) * (-1 if coords.group(4).upper() == "W" else 1)
    pt = PfmPoint(ugc=ugc, name=name or ugc, lat=lat, lon=lon,
                  elev_ft=int(coords.group(5)) if coords.group(5) else None,
                  expires=_expires(um.group(2), issued), utc_offset_h=0)

    # A block starts at its pair of hour-header rows and runs to the next
    # pair. (Not at the Date row: reception damage sometimes glues a Date
    # row onto the end of the line before it, and the times come from the
    # UTC hour row anyway.)
    starts: list[int] = []
    for k in range(i, len(lines)):
        if _HOUR_ROW.match(lines[k]) and not (starts and k - starts[-1] <= 2):
            starts.append(k)
    cursor = issued
    for n, s in enumerate(starts):
        block = lines[s:(starts[n + 1] if n + 1 < len(starts) else len(lines))]
        prev = [ln for ln in lines[max(i, s - 3):s] if ln.strip()]
        day = _anchor(prev[-1] if prev and _FIRST_DATE.search(prev[-1]) else None, issued)
        last = _parse_block(block, cursor, pt, after=n > 0, offset_hint=hint, first_day=day)
        if last is not None:
            cursor = last + timedelta(hours=1)
    return pt


# ---------------------------------------------------------------- summaries

_WX_ORDER = ["tstms", "mix", "snow", "snow_showers", "rain", "showers", "drizzle", "fog"]
_NOUN = {"tstms": "thunderstorms", "mix": "wintry mix", "snow": "snow", "snow_showers": "snow showers",
         "rain": "rain", "showers": "showers", "drizzle": "drizzle", "fog": "fog"}
_ICON = {"tstms": "tstorms", "mix": "mix", "snow": "snow", "snow_showers": "snow-showers", "rain": "rain",
         "showers": "showers", "drizzle": "drizzle", "fog": "fog"}


def _local(t: datetime, off: int) -> datetime:
    return t + timedelta(hours=off)


def _wx_kinds(wx: dict[str, str]) -> dict[str, int]:
    """Weather row codes -> {kind: likelihood rank}."""
    out: dict[str, int] = {}
    for name, code in wx.items():
        code = code.upper()
        if name == "obvis":
            if code in _FOG:
                out["fog"] = max(out.get("fog", 0), 3 if code in ("F", "FG", "+F") else 2)
            continue
        r = _LIKELY.get(code)
        if not r:
            continue
        kind = {"frzg_rain": "mix", "sleet": "mix"}.get(name, name)
        out[kind] = max(out.get(kind, 0), r)
    # Rain and snow together reads as a mix.
    if out.get("snow", 0) >= 2 and max(out.get("rain", 0), out.get("showers", 0)) >= 2:
        out["mix"] = max(out.get("mix", 0), min(out["snow"], max(out.get("rain", 0), out.get("showers", 0))))
    return out


def _half(entries: list[dict], period: dict | None, is_day: bool) -> dict | None:
    if not entries and not period:
        return None
    sky = Counter(e["clouds"] for e in entries if e.get("clouds"))
    clouds = max(sky, key=lambda c: (sky[c], _SKY_RANK.get(c, 0))) if sky else None
    wx: dict[str, int] = {}
    for e in entries:
        for k, r in _wx_kinds(e.get("wx", {})).items():
            wx[k] = max(wx.get(k, 0), r)
    speeds = [e["wspd"] for e in entries if "wspd" in e] or [_WCHAR[e["wchar"]] for e in entries
                                                              if e.get("wchar") in _WCHAR]
    gusts = [e["wgust"] for e in entries if "wgust" in e]
    dirs = Counter(e["wdir"] for e in entries if e.get("wdir"))
    h: dict = {
        "is_day": is_day,
        "clouds": clouds,
        "wx": wx,
        "pop": (period or {}).get("pop"),
        "qpf": (period or {}).get("qpf"),
        "temp": (period or {}).get("max" if is_day else "min"),
        "wind_dir": dirs.most_common(1)[0][0] if dirs else None,
        "wind_spd": max(speeds) if speeds else None,
        "wind_gust": max(gusts) if gusts else None,
        "wind_char": next((e["wchar"] for e in entries if e.get("wchar")), None),
    }
    if h["temp"] is None:
        # No 12-hour Max/Min for this half: fall back to the hourly temps,
        # but only over a whole half (4 three-hourly or 2 six-hourly cells),
        # never the last cell or two of the product.
        temps = [e["temp"] for e in entries if "temp" in e]
        if len(temps) >= (2 if entries and entries[0].get("step") == 6 else 4):
            h["temp"] = max(temps) if is_day else min(temps)
    h["condition"] = condition(h)
    return h


def condition(h: dict) -> dict:
    """{icon, text, night, badge} for a half-day (or one hourly entry)."""
    night = not h.get("is_day", True)
    pop = h.get("pop") or 0
    wx = h.get("wx") or {}
    sig = [k for k in _WX_ORDER if wx.get(k, 0) >= 2 or (wx.get(k, 0) >= 1 and pop >= 30)]
    if sig:
        # The most likely weather wins; between equals, the more significant.
        k = max(sig, key=lambda w: (wx[w], -_WX_ORDER.index(w)))
        r = wx[k]
        noun = _NOUN[k]
        if k == "fog":
            text = {2: "Patchy fog", 3: "Fog", 4: "Fog"}.get(r, "Patchy fog")
        elif r <= 1:
            text = f"Slight chance of {noun}"
        elif r == 2:
            text = f"Chance of {noun}"
        elif r == 3:
            text = f"{noun.capitalize()} likely"
        else:
            text = noun.capitalize()
        return {"icon": _ICON[k], "text": text, "night": night, "badge": None}
    c = h.get("clouds")
    if c in (None, "CL"):
        icon, text = "clear", ("Clear" if night else "Sunny")
    elif c == "FW":
        icon, text = "mostly-clear", ("Mostly clear" if night else "Mostly sunny")
    elif c == "SC":
        icon, text = "partly-cloudy", ("Partly cloudy" if night else "Partly sunny")
    elif c in ("B1", "B2"):
        icon, text = "mostly-cloudy", "Mostly cloudy"
    else:
        icon, text = "cloudy", "Cloudy"
    spd = h.get("wind_spd") or 0
    if spd >= 25 or h.get("wind_char") in ("WY", "VW", "SD", "HF"):
        icon, text = "windy", f"{text} and windy"
    elif spd >= 18 or h.get("wind_char") == "BZ":
        text = f"{text} and breezy"
    slight = next((k for k in _WX_ORDER if wx.get(k, 0) == 1), None)
    badge = f"{pop}%" if slight and pop >= 10 else None
    return {"icon": icon, "text": text, "night": night, "badge": badge}


def _pt(p: PfmPoint | dict) -> dict:
    return p if isinstance(p, dict) else p.as_row()


def _ts(v) -> datetime:
    return v if isinstance(v, datetime) else datetime.fromisoformat(v)


def daily(point: PfmPoint | dict) -> list[dict]:
    """One row per local date: {date, day: half|None, night: half|None}.

    Day = 06-18 local, night = 18 local to 06 the next morning (it belongs
    to the date it starts on). A half's high/low, PoP and QPF come from the
    12-hour period ending at its close; its sky, weather and wind from the
    hourly entries inside it.
    """
    p = _pt(point)
    off = p.get("utc_offset_h") or 0
    series = [{**e, "t": _ts(e["t"])} for e in p.get("series", [])]
    twelve = [{**x, "t": _ts(x["t"])} for x in p.get("twelve", [])]
    halves: dict[tuple[date, bool], dict] = {}

    def key(lt: datetime) -> tuple[date, bool]:
        if 6 <= lt.hour < 18:
            return lt.date(), True
        return (lt.date() if lt.hour >= 18 else lt.date() - timedelta(days=1)), False

    for e in series:
        lt = _local(e["t"], off)
        # A 6-hourly entry stands for the six hours AFTER its time
        # (an 18 local entry covers the evening, 12 local the afternoon).
        halves.setdefault(key(lt), {"entries": [], "period": None})["entries"].append(e)
    for x in twelve:
        lt = _local(x["t"], off)
        # Period ending at ~18 local is that day; ending at ~06 is the previous night.
        k = key(lt - timedelta(hours=6))
        halves.setdefault(k, {"entries": [], "period": None})["period"] = x

    days: dict[date, dict] = {}
    for (d, is_day), v in sorted(halves.items()):
        h = _half(v["entries"], v["period"], is_day)
        if h is None:
            continue
        row = days.setdefault(d, {"date": d.isoformat(), "day": None, "night": None})
        row["day" if is_day else "night"] = h
    rows = [days[d] for d in sorted(days)]
    for r in rows:
        r["hi"] = (r["day"] or {}).get("temp")
        r["lo"] = (r["night"] or {}).get("temp")
        pops = [h["pop"] for h in (r["day"], r["night"]) if h and h.get("pop") is not None]
        r["pop"] = max(pops) if pops else None
        qpfs = [h["qpf"] for h in (r["day"], r["night"]) if h and h.get("qpf") is not None]
        r["qpf"] = round(sum(qpfs), 2) if qpfs else None
    return rows


def hourly(point: PfmPoint | dict, day: str) -> list[dict]:
    """The point's entries on one local date, each with its own condition."""
    p = _pt(point)
    off = p.get("utc_offset_h") or 0
    twelve = sorted((_ts(x["t"]), x) for x in p.get("twelve", []))
    out = []
    for e in p.get("series", []):
        t = _ts(e["t"])
        lt = _local(t, off)
        if lt.date().isoformat() != day:
            continue
        period = next((x for end, x in twelve if end >= t), None)
        h = {"is_day": 6 <= lt.hour < 18, "clouds": e.get("clouds"), "wx": _wx_kinds(e.get("wx", {})),
             "pop": (period or {}).get("pop"), "wind_spd": e.get("wspd"), "wind_char": e.get("wchar")}
        out.append({"t": t.isoformat(), "local": lt.strftime("%H:%M"), "step": e.get("step"),
                    "temp": e.get("temp"), "dewpt": e.get("dewpt"), "pop": h["pop"], "wind_dir": e.get("wdir"),
                    "wind_spd": e.get("wspd"), "wind_gust": e.get("wgust"), "wind_char": e.get("wchar"),
                    "condition": condition(h)})
    return out


def _main_wx(h: dict | None) -> str | None:
    if not h:
        return None
    wx = h.get("wx") or {}
    sig = [k for k in _WX_ORDER if wx.get(k, 0) >= 2]
    return max(sig, key=lambda w: (wx[w], -_WX_ORDER.index(w))) if sig else None


def diff(prev: PfmPoint | dict, cur: PfmPoint | dict) -> list[dict]:
    """What changed between two issuances of the same point, per day.

    Only days both cover. A high or low that moved by 3 °F or more, a rain
    chance that moved by 20 points or more, or the main weather appearing,
    clearing or changing.
    """
    a = {r["date"]: r for r in daily(prev)}
    out: list[dict] = []
    for r in daily(cur):
        o = a.get(r["date"])
        if o is None:
            continue
        for f, label in (("hi", "high"), ("lo", "low")):
            if r.get(f) is not None and o.get(f) is not None and abs(r[f] - o[f]) >= 3:
                out.append({"date": r["date"], "field": f, "from": o[f], "to": r[f],
                            "text": f"{label} {'up' if r[f] > o[f] else 'down'} {abs(r[f] - o[f])}° "
                                    f"({o[f]}° → {r[f]}°)"})
        if r.get("pop") is not None and o.get("pop") is not None and abs(r["pop"] - o["pop"]) >= 20:
            out.append({"date": r["date"], "field": "pop", "from": o["pop"], "to": r["pop"],
                        "text": f"rain chance {'up' if r['pop'] > o['pop'] else 'down'} "
                                f"({o['pop']}% → {r['pop']}%)"})
        for half in ("day", "night"):
            w0, w1 = _main_wx(o.get(half)), _main_wx(r.get(half))
            if w0 != w1 and (r.get(half) and o.get(half)):
                when = "daytime" if half == "day" else "overnight"
                if w0 and not w1:
                    t = f"{_NOUN[w0]} no longer expected {when}"
                elif w1 and not w0:
                    t = f"{_NOUN[w1]} now expected {when}"
                else:
                    t = f"{when} {_NOUN[w0]} now {_NOUN[w1]}"
                out.append({"date": r["date"], "field": f"wx_{half}", "from": w0, "to": w1, "text": t})
    return out


def store_pfm(conn, product_id: int, nnn: str, office: str | None, raw: str, issued: datetime | None) -> int:
    """Every point of a PFM into pfm_point (replacing the product's rows, so
    --redecode can rerun it). Returns the number of points."""
    import json

    from sqlalchemy import text

    if nnn != "PFM" or issued is None:
        return 0
    pts = parse_pfm(raw, issued)
    conn.execute(text("DELETE FROM pfm_point WHERE product_id = :p"), {"p": product_id})
    for pt in pts:
        conn.execute(
            text(
                """
                INSERT INTO pfm_point (product_id, office, ugc, name, geom, elev_ft, issued, expires, utc_offset_h,
                                       series, twelve, extra)
                VALUES (:pid, :office, :ugc, :name, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), :elev, :issued,
                        :expires, :off, CAST(:series AS jsonb), CAST(:twelve AS jsonb), CAST(:extra AS jsonb))
                """
            ),
            {"pid": product_id, "office": office or "", "ugc": pt.ugc, "name": pt.name, "lon": pt.lon,
             "lat": pt.lat, "elev": pt.elev_ft, "issued": issued, "expires": pt.expires, "off": pt.utc_offset_h,
             "series": json.dumps(jsonable(pt.series)), "twelve": json.dumps(jsonable(pt.twelve)),
             "extra": json.dumps(pt.extra) if pt.extra else None},
        )
    return len(pts)


def jsonable(v):
    """Datetimes and dates to ISO strings, recursively (for jsonb columns)."""
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [jsonable(x) for x in v]
    return v


__all__ = ["PfmPoint", "parse_pfm", "daily", "hourly", "condition", "diff", "jsonable", "UTC"]
