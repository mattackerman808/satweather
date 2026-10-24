"""NHC / CPHC tropical cyclone products: the forecast/advisory (TCM) and the
tropical weather outlook (TWO), parsed into exact numbers.

The TCM is the machine-friendly one of each advisory package: center
position and its valid time, motion, pressure, maximum wind and gusts, the
34/50/64 kt wind radii by quadrant, the previous position, and every
forecast point (12 h ... 72 h) and outlook point (96, 120 h) with its wind
and radii. Everything here is what NHC wrote; nothing is interpolated (no
cone: NHC's cone is not in the text).

The storm's ATCF id (EP222026: basin, number, year) is in the header of
every product of its advisory package (TCP, TCM, TCD, PWS, TCA ...), which
is how products are tied to a storm.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

ATCF = re.compile(r"\b((AL|EP|CP)(\d{2})(\d{4}))\b")
STORM_NNN = {"TCM", "TCP", "TCD", "TCU", "PWS", "TCA", "TCE", "TCV", "TCB", "TCR"}
BASINS = {"AL": "Atlantic", "EP": "Eastern Pacific", "CP": "Central Pacific"}

_TYPES = {"POTENTIAL TROPICAL CYCLONE": "Potential Tropical Cyclone", "TROPICAL DEPRESSION": "Tropical Depression",
          "TROPICAL STORM": "Tropical Storm", "SUBTROPICAL DEPRESSION": "Subtropical Depression",
          "SUBTROPICAL STORM": "Subtropical Storm", "HURRICANE": "Hurricane", "TYPHOON": "Typhoon",
          "POST-TROPICAL CYCLONE": "Post-Tropical Cyclone", "REMNANTS OF": "Remnants of"}
_TITLE = re.compile(r"^(?P<what>[A-Z][A-Z .'-]+?)\s+(?:FORECAST/)?ADVISORY NUMBER\s+(?P<num>\d+[A-Z]?)", re.M)
_ISSUED = re.compile(r"^(\d{4}) UTC \w{3} (\w{3}) (\d{1,2}) (\d{4})\s*$", re.M)
_LL = r"(\d{1,2}\.\d)([NS])\s+(\d{1,3}\.\d)([EW])"
_CENTER = re.compile(r"CENTER LOCATED NEAR\s+" + _LL + r"\s+AT\s+(\d{2})/(\d{2})(\d{2})Z")
_PREV = re.compile(r"AT\s+(\d{2})/(\d{2})(\d{2})Z CENTER WAS LOCATED NEAR\s+" + _LL)
_MOVE = re.compile(r"PRESENT MOVEMENT TOWARD THE [A-Z-]+ OR\s+(\d{1,3}) DEGREES AT\s+(\d+) KT")
_PRESSURE = re.compile(r"MINIMUM CENTRAL PRESSURE\s+(\d{3,4}) MB")
_WIND = re.compile(r"MAX SUSTAINED WINDS\s+(\d+) KT WITH GUSTS TO\s+(\d+) KT")
_RADII = re.compile(r"^(64|50|34) KT\.+\s*(\d+)NE\s+(\d+)SE\s+(\d+)SW\s+(\d+)NW", re.M)
_POINT = re.compile(r"^(FORECAST|OUTLOOK) VALID (\d{2})/(\d{2})(\d{2})Z\s*(?:" + _LL + r")?\s*(?:\.\.\.(.*))?$", re.M)
_POINT_WIND = re.compile(r"MAX WIND\s+(\d+) KT\.\.\.GUSTS\s+(\d+) KT")
_MONTHS = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}


def category(wind_kt: int | None) -> str:
    """Saffir-Simpson category for a hurricane's wind, TS / TD below."""
    if wind_kt is None:
        return "?"
    for limit, cat in ((137, "5"), (113, "4"), (96, "3"), (83, "2"), (64, "1"), (34, "TS")):
        if wind_kt >= limit:
            return cat
    return "TD"


def _lat(v: str, h: str) -> float:
    return -float(v) if h == "S" else float(v)


def _lon(v: str, h: str) -> float:
    return -float(v) if h == "W" else float(v)


def _when(ref: datetime, day: int, hour: int, minute: int = 0) -> datetime:
    """ddhhmm relative to the issue time: across a month end if the day
    went backwards (issued the 30th, valid the 2nd)."""
    t = ref.replace(day=1, hour=hour, minute=minute, second=0, microsecond=0)
    if day < ref.day - 15:
        t = (t + timedelta(days=32)).replace(day=1)
    elif day > ref.day + 15:
        t = (t - timedelta(days=1)).replace(day=1)
    return t.replace(day=day)


def _radii(block: str) -> dict[str, list[int]]:
    return {m[1]: [int(m[2]), int(m[3]), int(m[4]), int(m[5])] for m in _RADII.finditer(block)}


@dataclass
class ForecastPoint:
    valid: datetime
    lat: float | None
    lon: float | None
    outlook: bool  # 96/120 h, from the "EXTENDED OUTLOOK"
    wind_kt: int | None = None
    gust_kt: int | None = None
    status: str | None = None  # "POST-TROP/EXTRATROP", "DISSIPATED", "INLAND" ...
    radii: dict[str, list[int]] = field(default_factory=dict)


@dataclass
class Advisory:
    storm_id: str
    basin: str
    number: str
    issued: datetime
    kind: str  # "Hurricane", "Tropical Storm", ...
    name: str
    lat: float
    lon: float
    valid: datetime
    move_dir: int | None
    move_kt: int | None
    pressure_mb: int | None
    wind_kt: int | None
    gust_kt: int | None
    radii: dict[str, list[int]]  # "64"/"50"/"34" -> [NE, SE, SW, NW] nautical miles
    prev: dict | None  # {"valid", "lat", "lon"}
    forecast: list[ForecastPoint]
    final: bool

    def as_row(self) -> dict:
        d = asdict(self)
        d["forecast"] = [{**f, "valid": f["valid"].isoformat()} for f in d["forecast"]]
        if d["prev"]:
            d["prev"] = {**d["prev"], "valid": d["prev"]["valid"].isoformat()}
        return d


def _kind_and_name(what: str) -> tuple[str, str]:
    what = " ".join(what.split())
    for t, label in _TYPES.items():
        if what.startswith(t + " ") or what == t:
            return label, what[len(t):].strip().title()
    return "", what.title()


def parse_tcm(text: str) -> Advisory | None:
    """A TCM's numbers, or None if it is not one we can read."""
    text = re.sub(r"[ \t]+\n", "\n", text.replace("\r", ""))
    head = "\n".join(text.split("\n")[:12])
    atcf, title, issued, center = ATCF.search(head), _TITLE.search(text), _ISSUED.search(text), _CENTER.search(text)
    if not (atcf and title and issued and center):
        return None
    mon = _MONTHS.get(issued[2].upper())
    if not mon:
        return None
    t = datetime(int(issued[4]), mon, int(issued[3]), int(issued[1][:2]), int(issued[1][2:]), tzinfo=UTC)
    kind, name = _kind_and_name(title["what"])

    # The current block runs from the center line to the first forecast.
    first_point = _POINT.search(text)
    current = text[center.start():first_point.start() if first_point else len(text)]
    move, pres, wind = _MOVE.search(current), _PRESSURE.search(current), _WIND.search(current)
    prev = _PREV.search(current)

    points = []
    starts = list(_POINT.finditer(text))
    for i, m in enumerate(starts):
        block = text[m.start():starts[i + 1].start() if i + 1 < len(starts) else len(text)]
        block = block.split("\n\n")[0] if "\n\n" in block else block
        pw = _POINT_WIND.search(block)
        points.append(ForecastPoint(
            valid=_when(t, int(m[2]), int(m[3]), int(m[4])),
            lat=_lat(m[5], m[6]) if m[5] else None, lon=_lon(m[7], m[8]) if m[7] else None,
            outlook=m[1] == "OUTLOOK",
            wind_kt=int(pw[1]) if pw else None, gust_kt=int(pw[2]) if pw else None,
            status=(m[9] or "").strip(" .") or None, radii=_radii(block)))

    return Advisory(
        storm_id=atcf[1], basin=atcf[2], number=title["num"], issued=t, kind=kind, name=name,
        lat=_lat(center[1], center[2]), lon=_lon(center[3], center[4]),
        valid=_when(t, int(center[5]), int(center[6]), int(center[7])),
        move_dir=int(move[1]) if move else None, move_kt=int(move[2]) if move else None,
        pressure_mb=int(pres[1]) if pres else None,
        wind_kt=int(wind[1]) if wind else None, gust_kt=int(wind[2]) if wind else None,
        radii=_radii(current),
        prev={"valid": _when(t, int(prev[1]), int(prev[2]), int(prev[3])), "lat": _lat(prev[4], prev[5]),
              "lon": _lon(prev[6], prev[7])} if prev else None,
        forecast=points,
        final=bool(re.search(r"THIS IS THE LAST (?:FORECAST/)?ADVISORY", text)),
    )


_TWO_ITEM_CHANCE = re.compile(r"Formation chance through (48 hours|7 days)\.+(\w+)\.+(?:near )?(\d+) percent", re.I)


def parse_two(text: str) -> dict:
    """A tropical weather outlook as {"none_expected", "active", "areas":
    [{"title", "text", "chance_48h", "chance_7d", "level_7d"}]}."""
    lines = [ln.rstrip() for ln in text.replace("\r", "").split("\n")]
    body = "\n".join(lines)
    out: dict = {"none_expected": bool(re.search(r"formation is not expected during the next 7 days", body, re.I)),
                 "active": None, "areas": []}
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    for p in paras:
        first, _, rest = p.partition("\n")
        if first.rstrip(":").lower() == "active systems":
            out["active"] = " ".join(rest.split())
            continue
        chances = {m[1].lower(): (int(m[3]), m[2].lower()) for m in _TWO_ITEM_CHANCE.finditer(p)}
        if chances and first.endswith(":"):
            prose = " ".join(ln for ln in rest.split("\n") if not ln.lstrip().startswith("*"))
            out["areas"].append({
                "title": first.rstrip(":"), "text": " ".join(prose.split()),
                "chance_48h": chances.get("48 hours", (None, None))[0],
                "chance_7d": chances.get("7 days", (None, None))[0],
                "level_7d": chances.get("7 days", (None, None))[1],
            })
    return out


def store_tropical(conn, product_id: int, nnn: str, raw: str) -> None:
    """Tie an advisory package product to its storm; read a TCM's numbers."""
    import json

    from sqlalchemy import text

    if nnn not in STORM_NNN:
        return
    m = ATCF.search("\n".join(raw.split("\n")[:14]))
    if not m:
        return
    conn.execute(text("UPDATE text_product SET storm_id = :s WHERE id = :p"), {"s": m[1], "p": product_id})
    if nnn != "TCM" or (a := parse_tcm(raw)) is None:
        return
    row = a.as_row()
    conn.execute(
        text(
            """
            INSERT INTO tc_advisory (storm_id, number, product_id, basin, issued, kind, name, lat, lon, valid,
                                     move_dir, move_kt, pressure_mb, wind_kt, gust_kt, radii, prev, forecast, final)
            VALUES (:storm_id, :number, :pid, :basin, :issued, :kind, :name, :lat, :lon, :valid, :move_dir,
                    :move_kt, :pressure_mb, :wind_kt, :gust_kt, CAST(:radii AS jsonb), CAST(:prev AS jsonb),
                    CAST(:forecast AS jsonb), :final)
            ON CONFLICT (storm_id, number) DO UPDATE SET product_id = EXCLUDED.product_id,
                issued = EXCLUDED.issued, kind = EXCLUDED.kind, name = EXCLUDED.name, lat = EXCLUDED.lat,
                lon = EXCLUDED.lon, valid = EXCLUDED.valid, move_dir = EXCLUDED.move_dir,
                move_kt = EXCLUDED.move_kt, pressure_mb = EXCLUDED.pressure_mb, wind_kt = EXCLUDED.wind_kt,
                gust_kt = EXCLUDED.gust_kt, radii = EXCLUDED.radii, prev = EXCLUDED.prev,
                forecast = EXCLUDED.forecast, final = EXCLUDED.final
            """
        ),
        {**row, "pid": product_id, "radii": json.dumps(row["radii"]), "prev": json.dumps(row["prev"]),
         "forecast": json.dumps(row["forecast"])},
    )
