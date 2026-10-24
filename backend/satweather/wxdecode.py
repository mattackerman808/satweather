"""METAR / SPECI / TAF / PIREP decoder — VENDORED from Tachbase.

Source: github.com/mattackerman808/tachbase-api,
        mcp/tachbase-mcp/tachbase_mcp/wxdecode.py @ d6177482e491
Copied unchanged below this header so fixes can flow both ways with a
plain diff. Fix bugs upstream first, then re-copy; tests/test_wxdecode.py
is Tachbase's own decoder test suite, vendored alongside.

Tachbase's original docstring follows.

Decode a METAR, SPECI, TAF or PIREP a pilot pasted, group by group.

Live weather comes back already decoded (AWC), but a report the pilot
types or pastes ("What does RAESNB42 mean?", "what's the ceiling in this
METAR?") had no decoder, and the Assistant read the codes from memory:
VV001 as "no ceiling", a PIREP's `SK 025 OVC 045` as "25% scattered",
TYR as Charlotte, the TAF's FM group at 0900Z as IFR at 3,500 ft
(graded_eval, 2026-10-22). Everything here is deterministic and follows
the AWH (FAA-H-8083-28A) and FMH-1 formats; a group it can't read comes
back under `not_decoded` with the raw text rather than a guess.

    decode("METAR KHOT 181253Z AUTO 00000KT M1/4SM FG VV001 08/08 A3000")
    decode("KHOU 151720Z 1518/1618 ... FM160900 34010KT P6SM VCTS ...", at="0900Z")
    decode("TYR UUA/OV TYR180015/TM 1757/FL310/TP B737/TB MOD-SEV CAT 350-390")
"""

from __future__ import annotations

import re
from fractions import Fraction

CEILING_RULE = ("Ceiling: the lowest layer reported broken (BKN) or overcast (OVC), or the "
                "vertical visibility (VV) into an obscuration (14 CFR 1.1). FEW and SCT "
                "layers are not ceilings.")
CATEGORY_RULE = ("Flight category (AWC): LIFR ceiling below 500 ft or visibility below 1 SM; "
                 "IFR ceiling 500 to below 1,000 ft or visibility 1 to below 3 SM; MVFR "
                 "ceiling 1,000-3,000 ft or visibility 3-5 SM; VFR ceiling above 3,000 ft "
                 "and visibility above 5 SM.")
# Asked how high a VFR flight can go under a decoded ceiling, the
# Assistant quoted 91.155's clearances from memory and applied the
# 1,000 ft above figure below the clouds (graded_eval, 2026-10-23).
VFR_RULE = ("VFR cloud clearance and visibility minimums depend on the airspace class "
            "and altitude (14 CFR 91.155): look them up with regulations_search before "
            "working out an altitude or legality from this weather.")

# ---------------------------------------------------------------------------
# Present weather (METAR/TAF/PIREP WX)
# ---------------------------------------------------------------------------

_DESCRIPTORS = {"MI": "shallow", "PR": "partial", "BC": "patches of", "DR": "low drifting",
                "BL": "blowing", "SH": "showers of", "TS": "thunderstorm", "FZ": "freezing"}
_PHENOMENA = {
    "DZ": "drizzle", "RA": "rain", "SN": "snow", "SG": "snow grains", "IC": "ice crystals",
    "PL": "ice pellets", "GR": "hail", "GS": "small hail/snow pellets",
    "UP": "unknown precipitation",
    "BR": "mist", "FG": "fog", "FU": "smoke", "VA": "volcanic ash", "DU": "widespread dust",
    "SA": "sand", "HZ": "haze", "PY": "spray",
    "PO": "dust/sand whirls", "SQ": "squalls", "FC": "funnel cloud", "SS": "sandstorm",
    "DS": "duststorm",
}
_WX = re.compile(r"^(?P<int>[-+]|VC)?(?P<body>(?:MI|PR|BC|DR|BL|SH|TS|FZ|DZ|RA|SN|SG|IC|PL|GR|GS"
                 r"|UP|BR|FG|FU|VA|DU|SA|HZ|PY|PO|SQ|FC|SS|DS)+)$")


_PRECIP = ("DZ", "RA", "SN", "SG", "IC", "PL", "GR", "GS", "UP")


def weather(code: str) -> str | None:
    """'+SN' → 'heavy snow', 'VCTS' → 'thunderstorm in the vicinity (5-10 SM
    from the airport)', 'BLSNFG' → 'blowing snow and fog', 'TSRA' →
    'thunderstorm with moderate rain'. None if it isn't a weather group.

    BL, DR, MI, PR and BC describe the phenomenon right after them; SH, FZ
    and TS the whole group; the intensity the precipitation."""
    m = _WX.match(code)
    if not m:
        return None
    body, intensity = m.group("body"), m.group("int")
    pairs = [body[i:i + 2] for i in range(0, len(body), 2)]
    if pairs == ["FC"] and intensity == "+":
        return "tornado or waterspout"
    names: list[str] = []
    pending = None
    for p in pairs:
        if p in ("TS", "SH", "FZ"):
            continue
        if p in _DESCRIPTORS:
            pending = _DESCRIPTORS[p]
            continue
        names.append(f"{pending} {_PHENOMENA[p]}" if pending else _PHENOMENA[p])
        pending = None
    if "FZ" in pairs:
        names = [f"freezing {n}" if n in ("rain", "drizzle", "fog") else n for n in names]
    first = next((k for k, p in enumerate(pairs) if p in _PRECIP), None)
    blown = first is not None and first > 0 and pairs[first - 1] in ("BL", "DR")
    if first is not None and (intensity in ("-", "+") or (intensity is None and not blown)):
        word = {"-": "light", "+": "heavy", None: "moderate"}[intensity]
        for k, p in enumerate(q for q in pairs if q not in _DESCRIPTORS):
            if p in _PRECIP:
                names[k] = f"{word} {names[k]}"
                break
    text = " and ".join(names)
    if "SH" in pairs:
        text = (f"{text} showers" if len(names) == 1 else f"showers of {text}") if names \
            else "showers"
    if "TS" in pairs:
        text = f"thunderstorm with {text}" if text else "thunderstorm"
    if intensity == "VC":
        text = f"{text} in the vicinity (5-10 SM from the airport)"
    return text


# ---------------------------------------------------------------------------
# Groups METARs and TAFs share
# ---------------------------------------------------------------------------

_WIND = re.compile(r"^(?P<dir>\d{3}|VRB)(?P<spd>\d{2,3})(?:G(?P<gst>\d{2,3}))?(?P<unit>KT|MPS)$")
_WIND_VAR = re.compile(r"^(\d{3})V(\d{3})$")
_VIS_SM = re.compile(r"^(?P<pm>[MP])?(?P<v>\d+|\d/\d{1,2}|\d+/\d{1,2})SM$")
_VIS_M = re.compile(r"^(\d{4})(?:NDV)?$")
_SKY = re.compile(r"^(?P<cov>FEW|SCT|BKN|OVC|VV)(?P<h>\d{3}|///)(?P<cb>CB|TCU)?$")
_TEMP = re.compile(r"^(?P<t>M?\d{2})/(?P<d>M?\d{2})?$")
_ALT = re.compile(r"^(?P<u>[AQ])(?P<v>\d{4})$")
_RVR = re.compile(r"^R(?P<rwy>\d{2}[LCR]?)/(?P<lo>[MP]?\d{4})(?:V(?P<hi>[MP]?\d{4}))?(?P<u>FT)?(?P<tr>[UDN])?$")
_WS = re.compile(r"^WS(?P<h>\d{3})/(?P<dir>\d{3})(?P<spd>\d{2,3})KT$")
_COVER = {"FEW": "few (1/8-2/8)", "SCT": "scattered (3/8-4/8)", "BKN": "broken (5/8-7/8)",
          "OVC": "overcast (8/8)"}


def _num(s: str) -> int:
    return -int(s[1:]) if s.startswith("M") else int(s)


def _vis_value(v: str) -> float:
    return float(sum(Fraction(p) for p in v.split()))


def wind_group(tok: str) -> dict | None:
    m = _WIND.match(tok)
    if not m:
        return None
    unit = "kt" if m["unit"] == "KT" else "m/s"
    spd = int(m["spd"])
    if m["dir"] == "000" and spd == 0:
        return {"meaning": "wind calm", "wind": {"calm": True}}
    d = "variable" if m["dir"] == "VRB" else f"from {m['dir']}° true"
    text = f"wind {d} at {spd} {unit}"
    w = {"direction": None if m["dir"] == "VRB" else int(m["dir"]), "speed": spd, "unit": unit}
    if m["gst"]:
        text += f", gusting {int(m['gst'])} {unit}"
        w["gust"] = int(m["gst"])
    return {"meaning": text, "wind": w}


def sky_group(tok: str) -> dict | None:
    if tok in ("SKC", "CLR"):
        return {"meaning": "sky clear" + (" (automated: no clouds below 12,000 ft)"
                                          if tok == "CLR" else ""), "layer": None}
    if tok in ("NSC", "NCD"):
        return {"meaning": "no significant cloud" if tok == "NSC" else "no cloud detected",
                "layer": None}
    m = _SKY.match(tok)
    if not m:
        return None
    if m["h"] == "///":
        return {"meaning": f"{m['cov']} layer, height not measured", "layer": None}
    h = int(m["h"]) * 100
    if m["cov"] == "VV":
        return {"meaning": f"vertical visibility {h:,} ft: sky obscured, an indefinite ceiling "
                           f"at {h:,} ft AGL", "layer": {"cover": "VV", "base_ft_agl": h}}
    # "Base" spelled out: "BKN035CB" read as "broken at 3,500 ft AGL,
    # cumulonimbus" came back as "CB tops at 3,500 ft" (graded_eval faa_atm_70).
    kind = {"CB": " cumulonimbus", "TCU": " towering cumulus"}.get(m["cb"] or "", "")
    cover, frac = _COVER[m["cov"]].split(" ")
    tops = " (METAR/TAF heights are cloud bases; tops aren't reported)" if kind else ""
    return {"meaning": f"{cover}{kind} {frac}, base {h:,} ft AGL{tops}",
            "layer": {"cover": m["cov"], "base_ft_agl": h, **({"type": m["cb"]} if m["cb"] else {})}}


def ceiling(layers: list[dict]) -> int | None:
    for lay in layers:
        if lay["cover"] in ("BKN", "OVC", "VV"):
            return lay["base_ft_agl"]
    return None


def flight_category(ceiling_ft: int | None, vis_sm: float | None) -> str | None:
    if vis_sm is None and ceiling_ft is None:
        return None
    c = 100000 if ceiling_ft is None else ceiling_ft
    v = 99.0 if vis_sm is None else vis_sm
    if c < 500 or v < 1:
        return "LIFR"
    if c < 1000 or v < 3:
        return "IFR"
    if c <= 3000 or v <= 5:
        return "MVFR"
    return "VFR"


class _Groups:
    """Decodes the shared body groups of a METAR or a TAF period."""

    def __init__(self) -> None:
        self.decoded: list[dict] = []
        self.not_decoded: list[str] = []
        self.layers: list[dict] = []
        self.vis_sm: float | None = None
        self.vis_text: str | None = None
        self.wind: dict | None = None
        self.wx: list[str] = []

    def add(self, group: str, meaning: str) -> None:
        self.decoded.append({"group": group, "meaning": meaning})

    def take(self, toks: list[str], i: int) -> int:
        """Decode toks[i] (and a following token it pairs with); returns the
        next index."""
        tok = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if (w := wind_group(tok)):
            self.add(tok, w["meaning"])
            self.wind = w["wind"]
            return i + 1
        if (m := _WIND_VAR.match(tok)):
            self.add(tok, f"wind direction varying between {m[1]}° and {m[2]}°")
            return i + 1
        if re.fullmatch(r"\d+", tok) and re.fullmatch(r"\d/\d{1,2}SM", nxt):
            self.vis_sm = _vis_value(f"{tok} {nxt[:-2]}")
            self.vis_text = f"{tok} {nxt[:-2]} SM"
            self.add(f"{tok} {nxt}", f"visibility {tok} {nxt[:-2]} statute miles")
            return i + 2
        if (m := _VIS_SM.match(tok)):
            v = _vis_value(m["v"])
            pre = {"M": "less than ", "P": "greater than "}.get(m["pm"] or "", "")
            self.vis_sm = v - 0.01 if m["pm"] == "M" else v + 0.01 if m["pm"] == "P" else v
            self.vis_text = f"{pre}{m['v']} SM"
            self.add(tok, f"visibility {pre}{m['v']} statute mile{'s' if v > 1 else ''}")
            return i + 1
        if (m := _VIS_M.match(tok)):
            meters = int(m[1])
            if meters == 9999:
                self.vis_sm, self.vis_text = 6.3, "10 km or more"
            else:
                self.vis_sm = round(meters / 1609.34, 2)
                self.vis_text = f"{meters} m (about {self.vis_sm} SM)"
            self.add(tok, f"visibility {self.vis_text}")
            return i + 1
        if (m := _RVR.match(tok)):
            unit = "ft" if m["u"] else "m"

            def rv(x: str) -> str:
                pre = {"M": "less than ", "P": "more than "}.get(x[0], "")
                return f"{pre}{int(x.lstrip('MP')):,} {unit}"
            text = f"runway {m['rwy']} visual range {rv(m['lo'])}"
            if m["hi"]:
                text = f"runway {m['rwy']} visual range variable from {rv(m['lo'])} to {rv(m['hi'])}"
            if m["tr"]:
                text += {"U": ", increasing", "D": ", decreasing", "N": ", no change"}[m["tr"]]
            self.add(tok, text)
            return i + 1
        if (s := sky_group(tok)):
            self.add(tok, s["meaning"])
            if s["layer"]:
                self.layers.append(s["layer"])
            return i + 1
        if (wx := weather(tok)):
            self.add(tok, wx)
            self.wx.append(wx)
            return i + 1
        if tok == "NSW":
            self.add(tok, "no significant weather")
            return i + 1
        if tok == "CAVOK":
            self.vis_sm, self.vis_text = 6.3, "10 km or more"
            self.add(tok, "ceiling and visibility OK: visibility 10 km or more, no cloud below "
                          "5,000 ft or the highest minimum sector altitude, no CB/TCU, no "
                          "significant weather")
            return i + 1
        if (m := _WS.match(tok)):
            self.add(tok, f"low-level wind shear at {int(m['h']) * 100:,} ft AGL, wind from "
                          f"{m['dir']}° at {int(m['spd'])} kt")
            return i + 1
        self.not_decoded.append(tok)
        return i + 1

    def summary(self) -> dict:
        c = ceiling(self.layers)
        out: dict = {"ceiling": f"{c:,} ft AGL" if c is not None else
                     "none (no broken, overcast or obscured layer reported)"}
        if self.vis_text:
            out["visibility"] = self.vis_text
        cat = flight_category(c, self.vis_sm)
        if cat:
            out["flight_category"] = cat
        if self.wx:
            out["weather"] = self.wx
        return out


# ---------------------------------------------------------------------------
# METAR / SPECI
# ---------------------------------------------------------------------------

_STATION = re.compile(r"^[A-Z][A-Z0-9]{3}$")
_DAYTIME = re.compile(r"^(\d{2})(\d{2})(\d{2})Z$")


def _ordinal(d: int) -> str:
    return f"{d}{'th' if 11 <= d % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(d % 10, 'th')}"


def decode_metar(text: str) -> dict:
    body, _, rmk = text.partition(" RMK ")
    toks = body.split()
    g = _Groups()
    out: dict = {"type": "METAR"}
    i = 0
    if toks and toks[0] in ("METAR", "SPECI"):
        out["type"] = toks[0]
        if toks[0] == "SPECI":
            g.add("SPECI", "special (unscheduled) observation")
        i = 1
    if i < len(toks) and _STATION.match(toks[i]):
        out["station"] = toks[i]
        g.add(toks[i], f"station {toks[i]}")
        i += 1
    if i < len(toks) and (m := _DAYTIME.match(toks[i])):
        out["observed"] = toks[i]
        g.add(toks[i], f"observed on the {_ordinal(int(m[1]))} at {m[2]}{m[3]}Z")
        i += 1
    while i < len(toks):
        tok = toks[i]
        if tok == "AUTO":
            g.add(tok, "fully automated report, no human observer")
            i += 1
        elif tok == "COR":
            g.add(tok, "corrected report")
            i += 1
        elif (m := _TEMP.match(tok)):
            t = _num(m["t"])
            text_ = f"temperature {t}°C"
            if m["d"]:
                text_ += f", dew point {_num(m['d'])}°C"
                out["temp_dewpoint_spread_c"] = t - _num(m["d"])
            g.add(tok, text_)
            i += 1
        elif (m := _ALT.match(tok)):
            if m["u"] == "A":
                g.add(tok, f"altimeter {int(m['v']) / 100:.2f} inHg")
            else:
                g.add(tok, f"QNH {int(m['v'])} hPa")
            i += 1
        elif tok in ("NOSIG", "TEMPO", "BECMG"):
            g.add(tok, {"NOSIG": "no significant change expected in the next 2 hours",
                        "TEMPO": "trend: temporarily", "BECMG": "trend: becoming"}[tok])
            i += 1
        else:
            i = g.take(toks, i)
    out["decoded"] = g.decoded
    out["summary"] = g.summary()
    if rmk.strip():
        rd, rn = decode_remarks(rmk)
        out["remarks"] = rd
        g.not_decoded += rn
    if g.not_decoded:
        out["not_decoded"] = g.not_decoded
    return out


# Remarks: the common ASOS/AWOS and observer remarks (FMH-1 ch. 12).
_RMK_FIXED = {
    "AO1": "automated station without a precipitation discriminator (can't tell rain from snow)",
    "AO2": "automated station with a precipitation discriminator",
    "AO1A": "automated station without a precipitation discriminator, observer augmented",
    "AO2A": "automated station with a precipitation discriminator, observer augmented",
    "$": "the station needs maintenance",
    "TSNO": "thunderstorm sensor not available",
    "PNO": "precipitation amount not available",
    "FZRANO": "freezing rain sensor not available",
    "RVRNO": "runway visual range not available",
    "PWINO": "present weather sensor not available",
    "VISNO": "visibility at a second location not available",
    "CHINO": "cloud-height sensor at a second location not available",
    "SLPNO": "sea-level pressure not available",
    "PRESRR": "pressure rising rapidly",
    "PRESFR": "pressure falling rapidly",
    "FROPA": "frontal passage",
    "VIRGA": "virga (precipitation not reaching the ground)",
    "ACSL": "altocumulus standing lenticular clouds",
    "CBMAM": "cumulonimbus mammatus",
    "OCNL": "occasional", "FRQ": "frequent", "CONS": "continuous",
    "DSNT": "distant (more than 10 SM)", "VC": "in the vicinity",
    "OHD": "overhead", "MOV": "moving", "ALQDS": "all quadrants",
    "LTGIC": "lightning in cloud", "LTGCC": "lightning cloud to cloud",
    "LTGCG": "lightning cloud to ground", "LTGCA": "lightning cloud to air",
    "LTG": "lightning", "CB": "cumulonimbus", "TCU": "towering cumulus",
}
_BEGIN_END = re.compile(r"^(?:(?:[-+]|VC)?(?:MI|PR|BC|DR|BL|SH|TS|FZ)?"
                        r"(?:DZ|RA|SN|SG|IC|PL|GR|GS|UP|TS|FG|BR|FZRA|FZDZ)+(?:[BE]\d{0,4})+)+$")
_BE_PART = re.compile(r"((?:[-+]|VC)?[A-Z]+?)((?:[BE]\d{0,4})+)")
_BE_EVENT = re.compile(r"([BE])(\d{0,4})")


def _begin_end(tok: str) -> str | None:
    """'RAB15E30' → rain began :15 and ended :30; 'RAESNB42' → rain ended and
    snow began at :42 (a B/E without a time takes the next time given)."""
    if not _BEGIN_END.match(tok):
        return None
    events: list[list[str]] = []          # [phenomenon, began/ended, time]
    for wx, seq in _BE_PART.findall(tok):
        name = re.sub(r"\b(moderate|light|heavy) ", "", weather(wx) or wx)
        for be, t in _BE_EVENT.findall(seq):
            events.append([name, "began" if be == "B" else "ended", t])
    pending = None
    for ev in reversed(events):
        if ev[2]:
            pending = ev[2]
        elif pending:
            ev[2] = pending
    if not events or not all(ev[2] for ev in events):
        return None

    def when(t: str) -> str:
        return f"{t[:2]}{t[2:]}Z" if len(t) == 4 else f"{int(t)} minutes past the hour"
    parts = [f"{n} {be} at {when(t)}" for n, be, t in events]
    return "; ".join(parts)


def decode_remarks(rmk: str) -> tuple[list[dict], list[str]]:
    toks = rmk.split()
    out: list[dict] = []
    left: list[str] = []
    i = 0
    while i < len(toks):
        tok = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        m = None
        if tok in _RMK_FIXED:
            out.append({"group": tok, "meaning": _RMK_FIXED[tok]})
        elif (m := re.fullmatch(r"SLP(\d{3})", tok)):
            v = int(m[1]) / 10
            hpa = 1000 + v if v < 50 else 900 + v
            out.append({"group": tok, "meaning": f"sea-level pressure {hpa:.1f} hPa"})
        elif (m := re.fullmatch(r"T([01])(\d{3})([01])(\d{3})", tok)):
            t = int(m[2]) / 10 * (-1 if m[1] == "1" else 1)
            d = int(m[4]) / 10 * (-1 if m[3] == "1" else 1)
            out.append({"group": tok, "meaning": f"temperature {t:.1f}°C, dew point {d:.1f}°C (tenths)"})
        elif (m := re.fullmatch(r"T([01])(\d{3})", tok)):
            t = int(m[2]) / 10 * (-1 if m[1] == "1" else 1)
            out.append({"group": tok, "meaning": f"temperature {t:.1f}°C (tenths)"})
        elif (m := re.fullmatch(r"P(\d{4})", tok)):
            out.append({"group": tok, "meaning": "trace of precipitation in the past hour"
                        if m[1] == "0000" else f"{int(m[1]) / 100:.2f} in of precipitation in the past hour"})
        elif (m := re.fullmatch(r"6(\d{4}|////)", tok)):
            out.append({"group": tok, "meaning": "3- or 6-hour precipitation amount: "
                        + ("not measured" if m[1] == "////" else f"{int(m[1]) / 100:.2f} in")})
        elif (m := re.fullmatch(r"7(\d{4})", tok)):
            out.append({"group": tok, "meaning": f"24-hour precipitation {int(m[1]) / 100:.2f} in"})
        elif (m := re.fullmatch(r"([12])([01])(\d{3})", tok)):
            t = int(m[3]) / 10 * (-1 if m[2] == "1" else 1)
            out.append({"group": tok, "meaning": f"6-hour {'maximum' if m[1] == '1' else 'minimum'} "
                                                 f"temperature {t:.1f}°C"})
        elif (m := re.fullmatch(r"4([01])(\d{3})([01])(\d{3})", tok)):
            hi = int(m[2]) / 10 * (-1 if m[1] == "1" else 1)
            lo = int(m[4]) / 10 * (-1 if m[3] == "1" else 1)
            out.append({"group": tok, "meaning": f"24-hour maximum {hi:.1f}°C, minimum {lo:.1f}°C"})
        elif (m := re.fullmatch(r"5([0-8])(\d{3})", tok)):
            trend = {"0": "increasing, then decreasing", "1": "increasing, then steady",
                     "2": "increasing", "3": "decreasing or steady, then increasing",
                     "4": "steady", "5": "decreasing, then increasing",
                     "6": "decreasing, then steady", "7": "decreasing",
                     "8": "steady or increasing, then decreasing"}[m[1]]
            out.append({"group": tok, "meaning": f"3-hour pressure tendency: {trend}, "
                                                 f"{int(m[2]) / 10:.1f} hPa change"})
        elif tok == "PK" and nxt == "WND" and i + 2 < len(toks) and \
                (m := re.fullmatch(r"(\d{3})(\d{2,3})/(\d{2,4})", toks[i + 2])):
            t = m[3] if len(m[3]) == 4 else f":{m[3]}"
            out.append({"group": f"PK WND {toks[i + 2]}",
                        "meaning": f"peak wind from {m[1]}° at {int(m[2])} kt at {t}"
                                   + ("Z" if len(m[3]) == 4 else " past the hour")})
            i += 3
            continue
        elif tok == "WSHFT" and (m := re.fullmatch(r"(\d{2,4})", nxt)):
            out.append({"group": f"WSHFT {nxt}", "meaning": f"wind shift at {nxt}"
                        + ("Z" if len(nxt) == 4 else " minutes past the hour")})
            i += 2
            continue
        elif tok in ("CIG", "VIS") and (m := re.fullmatch(r"(\d{3})V(\d{3})", nxt)):
            out.append({"group": f"{tok} {nxt}", "meaning": f"variable ceiling {int(m[1]) * 100:,} "
                                                           f"to {int(m[2]) * 100:,} ft"})
            i += 2
            continue
        elif tok == "VIS" and nxt:
            j = i + 1
            vis = [nxt]
            if j + 1 < len(toks) and re.fullmatch(r"\d/\d{1,2}V.*", toks[j + 1]):
                vis.append(toks[j + 1])
            out.append({"group": "VIS " + " ".join(vis), "meaning": "variable visibility "
                        + " ".join(vis).replace("V", " to ") + " SM"})
            i += 1 + len(vis)
            continue
        elif (be := _begin_end(tok)):
            out.append({"group": tok, "meaning": be})
        elif (m := re.fullmatch(r"(?:TWR|SFC) VIS", f"{tok} {nxt}")):
            out.append({"group": f"{tok} VIS", "meaning": f"{'tower' if tok == 'TWR' else 'surface'} "
                                                          "visibility (the value follows)"})
            i += 2
            continue
        elif re.fullmatch(r"[NESW]{1,2}(-[NESW]{1,2})?", tok):
            out.append({"group": tok, "meaning": f"direction {tok}"})
        else:
            left.append(tok)
        i += 1
    return out, left


# ---------------------------------------------------------------------------
# TAF
# ---------------------------------------------------------------------------

_VALID = re.compile(r"^(\d{2})(\d{2})/(\d{2})(\d{2})$")
_FM = re.compile(r"^FM(\d{2})(\d{2})(\d{2})$")


class _Clock:
    """Minutes since the TAF's validity start, wrapping the month."""

    def __init__(self, day: int, hour: int) -> None:
        self.day, self.hour = day, hour

    def at(self, day: int, hour: int, minute: int = 0) -> int:
        d = day - self.day
        if d < -15:
            d += 31
        return d * 1440 + (hour - self.hour) * 60 + minute


def _when(day: int, hour: int, minute: int = 0) -> str:
    return f"the {_ordinal(day)} at {hour:02d}{minute:02d}Z"


def decode_taf(text: str, at: str = "") -> dict:
    toks = text.split()
    out: dict = {"type": "TAF"}
    i = 0
    head = _Groups()
    if toks and toks[0] == "TAF":
        i = 1
    while i < len(toks) and toks[i] in ("AMD", "COR", "RTD"):
        head.add(toks[i], {"AMD": "amended forecast", "COR": "corrected forecast",
                           "RTD": "routine delayed"}[toks[i]])
        i += 1
    if i < len(toks) and _STATION.match(toks[i]):
        out["station"] = toks[i]
        head.add(toks[i], f"station {toks[i]}")
        i += 1
    if i < len(toks) and (m := _DAYTIME.match(toks[i])):
        head.add(toks[i], f"issued on the {_ordinal(int(m[1]))} at {m[2]}{m[3]}Z")
        i += 1
    if i >= len(toks) or not (v := _VALID.match(toks[i])):
        return {"type": "TAF", "error": "no valid period (DDHH/DDHH) found", "text": text}
    d1, h1, d2, h2 = map(int, v.groups())
    clock = _Clock(d1, h1)
    end = clock.at(d2, h2)
    head.add(toks[i], f"valid from {_when(d1, h1)} to {_when(d2, h2)}")
    out["header"] = head.decoded
    i += 1

    periods: list[dict] = []

    def new(kind: str, label: str, start: int, stop: int | None, group: str) -> dict:
        p = {"change": kind, "group": group, "label": label, "start": start, "stop": stop,
             "g": _Groups()}
        periods.append(p)
        return p

    cur = new("BASE", f"from {_when(d1, h1)}", 0, None, toks[i - 1])
    while i < len(toks):
        tok = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if (m := _FM.match(tok)):
            d, h, mi = map(int, m.groups())
            cur = new("FM", f"from {_when(d, h, mi)}: conditions change completely to",
                      clock.at(d, h, mi), None, tok)
            i += 1
            continue
        if tok in ("TEMPO", "BECMG") and (w := _VALID.match(nxt)):
            a, b, c, e = map(int, w.groups())
            label = {"TEMPO": f"temporarily (less than half the time) from {_when(a, b)} to "
                              f"{_when(c, e)}",
                     "BECMG": f"becoming, gradually between {_when(a, b)} and {_when(c, e)}"}[tok]
            cur = new(tok, label, clock.at(a, b), clock.at(c, e), f"{tok} {nxt}")
            i += 2
            continue
        if (pm := re.fullmatch(r"PROB(\d{2})", tok)):
            j = i + 1
            kind = f"PROB{pm[1]}"
            if j < len(toks) and toks[j] == "TEMPO":
                kind += " TEMPO"
                j += 1
            if j < len(toks) and (w := _VALID.match(toks[j])):
                a, b, c, e = map(int, w.groups())
                cur = new(kind, f"{pm[1]}% probability from {_when(a, b)} to {_when(c, e)}",
                          clock.at(a, b), clock.at(c, e), " ".join(toks[i:j + 1]))
                i = j + 1
                continue
        i = cur["g"].take(toks, i)

    fms = [p for p in periods if p["change"] in ("BASE", "FM")]
    for a, b in zip(fms, fms[1:] + [None]):
        a["stop"] = b["start"] if b else end
    out["periods"] = []
    not_decoded: list[str] = []
    for p in periods:
        g = p["g"]
        entry = {"group": p["group"], "when": p["label"], "decoded": g.decoded,
                 "summary": g.summary()}
        out["periods"].append(entry)
        not_decoded += g.not_decoded
    if not_decoded:
        out["not_decoded"] = not_decoded
    out["note"] = ("FM starts a new set of conditions that replaces everything before it. "
                   "TEMPO, PROB and BECMG modify the FM period they fall in, only for their own "
                   "time window. TAF cloud heights are AGL.")
    if at:
        out["at"] = _taf_at(at, periods, clock, d1, h1, end)
    return out


def _taf_at(at: str, periods: list[dict], clock: _Clock, d1: int, h1: int, end: int) -> dict:
    """What the TAF forecasts at `at` ("0900Z", "160900Z", "1609")."""
    s = at.strip().upper().rstrip("Z")
    if re.fullmatch(r"\d{6}", s):
        cands = [clock.at(int(s[:2]), int(s[2:4]), int(s[4:]))]
    elif re.fullmatch(r"\d{4}", s):
        hh, mm = int(s[:2]), int(s[2:])
        cands = [clock.at(d1, hh, mm) + k * 1440 for k in range(0, 3)]
    else:
        return {"error": f"time {at!r} not understood: use HHMMZ or DDHHMMZ"}
    t = next((c for c in cands if 0 <= c < end), None)
    if t is None:
        return {"error": f"{at} is outside the TAF's valid period"}
    day, rem = divmod(t + h1 * 60, 1440)
    label = f"{(d1 + day - 1) % 31 + 1:02d}{rem // 60:02d}{rem % 60:02d}Z"
    prevailing = next(p for p in reversed([p for p in periods if p["change"] in ("BASE", "FM")])
                      if p["start"] <= t)
    also = [p for p in periods if p["change"] not in ("BASE", "FM")
            and p["start"] <= t < (p["stop"] or end)]
    becmg_done = [p for p in periods if p["change"] == "BECMG" and (p["stop"] or 0) <= t
                  and p["start"] >= prevailing["start"]]

    def brief(p: dict) -> dict:
        return {"group": p["group"], "when": p["label"],
                "decoded": [d["meaning"] for d in p["g"].decoded], "summary": p["g"].summary()}
    res = {"time": label, "prevailing": brief(prevailing)}
    if becmg_done:
        res["changed_by_becmg"] = [brief(p) for p in becmg_done]
    if also:
        res["also_possible"] = [brief(p) for p in also]
    else:
        res["also_possible"] = []
    return res


# ---------------------------------------------------------------------------
# PIREP
# ---------------------------------------------------------------------------

_PIREP_FIELDS = ("OV", "TM", "FL", "TP", "SK", "WX", "TA", "WV", "TB", "IC", "RM")
# A field starts at "/" + its code, not followed by a letter ("/FL310",
# "/OV TYR180015"; a sky layer's "/OVC" isn't one).
_PIREP_SPLIT = re.compile(r"/\s*(?=(?:%s)(?![A-Z]))" % "|".join(_PIREP_FIELDS))
_TB_INT = {"NEG": "none", "SMTH": "smooth", "SMTH-LGT": "smooth to light", "LGT": "light",
           "LGT-MOD": "light to moderate", "MOD": "moderate", "MOD-SEV": "moderate to severe",
           "SEV": "severe", "SEV-EXTRM": "severe to extreme", "EXTRM": "extreme"}
_IC_INT = {"NEG": "none", "NEGCLR": "none", "TRACE": "trace", "TRC": "trace",
           "TRACE-LGT": "trace to light", "TRC-LGT": "trace to light", "LGT": "light",
           "LGT-MOD": "light to moderate", "MOD": "moderate", "MOD-SEV": "moderate to severe",
           "SEV": "severe"}
_FREQ = {"OCNL": "occasional", "INTMT": "intermittent", "CONS": "continuous"}


def _alt_range(s: str) -> str | None:
    """'350-390' → 'FL350 to FL390' style text in feet MSL; 'BLO 090', 'ABV 100'."""
    m = re.fullmatch(r"(\d{3})-(\d{3})", s)
    if m:
        return f"between {int(m[1]) * 100:,} and {int(m[2]) * 100:,} ft MSL"
    m = re.fullmatch(r"(\d{3})", s)
    if m:
        return f"at {int(m[1]) * 100:,} ft MSL"
    m = re.fullmatch(r"(BLO|ABV)\s*(\d{3})", s)
    if m:
        return f"{'below' if m[1] == 'BLO' else 'above'} {int(m[2]) * 100:,} ft MSL"
    return None


def _pirep_location(s: str) -> tuple[str, list[str]]:
    s = s.strip()
    if (m := re.fullmatch(r"([A-Z0-9]{3,4})(\d{3})(\d{3})", s)):
        points = ("north", "northeast", "east", "southeast", "south", "southwest", "west",
                  "northwest")
        return (f"{int(m[3])} NM {points[round(int(m[2]) / 45) % 8]} of {m[1]} (its {m[2]}° "
                f"radial)", [m[1]])
    if (m := re.fullmatch(r"(\d{1,3})\s?([NESW]{1,3})\s+([A-Z0-9]{3,4})", s)):
        return f"{int(m[1])} NM {m[2]} of {m[3]}", [m[3]]
    if (m := re.fullmatch(r"([A-Z0-9]{3,4})-([A-Z0-9]{3,4})", s)):
        return f"along the route from {m[1]} to {m[2]}", [m[1], m[2]]
    if re.fullmatch(r"[A-Z0-9]{3,4}", s):
        return f"over {s}", [s]
    return s, []


def _pirep_sky(s: str) -> str:
    """'025 OVC 045/075 OVC 080/090 OVC' (base cover top, layers split by
    '/') or the newer 'BKN030-TOP045'. Heights are hundreds of ft MSL."""
    layers = []
    for lay in [x.strip() for x in s.split("/") if x.strip()]:
        if lay in ("SKC", "CLR"):
            layers.append("sky clear")
            continue
        m = re.fullmatch(r"(?:(\d{3}|UNKN)\s*)?(FEW|SCT|BKN|OVC|SKC)(?:-(FEW|SCT|BKN|OVC))?"
                         r"(\d{3})?(?:\s*-?\s*TOP\s*(\d{3}|UNKN)|\s+(\d{3}|UNKN))?", lay)
        if not m:
            layers.append(f"{lay} (not decoded)")
            continue
        base = m[1] or m[4]
        top = m[5] or m[6]
        cov = _COVER.get(m[2], m[2]).split(" (")[0]
        if m[3]:
            cov += f" to {_COVER[m[3]].split(' (')[0]}"
        text = cov
        text += (f", base {int(base) * 100:,} ft MSL" if base and base != "UNKN"
                 else ", base unknown" if base == "UNKN" else "")
        text += (f", top {int(top) * 100:,} ft MSL" if top and top != "UNKN"
                 else ", top unknown" if top == "UNKN" else "")
        layers.append(text)
    n = len(layers)
    return f"{n} layer{'s' if n != 1 else ''}: " + "; ".join(layers)


def decode_pirep(text: str) -> dict:
    parts = [p.strip() for p in _PIREP_SPLIT.split(text.strip())]
    head = parts[0].split()
    out: dict = {"type": "PIREP", "decoded": []}
    idents: list[str] = []
    not_decoded: list[str] = []

    def add(group: str, meaning: str) -> None:
        out["decoded"].append({"group": group, "meaning": meaning})
    for h in head:
        if h == "UA":
            add(h, "routine pilot report")
        elif h == "UUA":
            add(h, "URGENT pilot report (severe or extreme turbulence or icing, LLWS, "
                   "tornado/funnel cloud, volcanic ash, hail, or similar hazard)")
        elif re.fullmatch(r"[A-Z0-9]{3,4}", h):
            out["station"] = h
            idents.append(h)
            add(h, f"report filed through {h} (nearest weather reporting location or "
                   f"navaid)")
        else:
            not_decoded.append(h)
    for part in parts[1:]:
        code, val = part[:2], part[2:].strip()
        if code == "OV":
            if (m := re.fullmatch(r"(.+?)\s+(\d{4})Z?", val)):    # time left in OV
                val = m[1]
                add(m[2], f"time: {m[2]}Z")
            where, ids = _pirep_location(val)
            idents += ids
            add(part, f"location: {where}")
        elif code == "TM":
            add(part, f"time: {val}Z" if re.fullmatch(r"\d{4}", val) else f"time: {val}")
        elif code == "FL":
            if re.fullmatch(r"\d{3}", val):
                ft = int(val) * 100
                add(part, f"altitude: FL{val} ({ft:,} ft)" if ft >= 18000
                    else f"altitude: {ft:,} ft MSL")
            else:
                add(part, {"UNKN": "altitude unknown", "DURC": "during climb",
                           "DURD": "during descent"}.get(val, f"altitude: {val}"))
        elif code == "TP":
            add(part, f"aircraft type: {val}")
        elif code == "SK":
            add(part, "sky cover: " + _pirep_sky(val))
        elif code == "WX":
            words = []
            for w in val.split():
                if (m := re.fullmatch(r"FV(\d{2,3})SM", w)):
                    words.append(f"flight visibility {int(m[1])} SM")
                elif (wx := weather(w)):
                    words.append(wx)
                else:
                    words.append(w)
            add(part, "weather: " + ", ".join(words))
        elif code == "TA":
            m = re.fullmatch(r"(M?)(\d{1,2})", val)
            add(part, f"outside air temperature {'-' if m and m[1] else ''}{int(m[2])}°C"
                if m else f"temperature {val}")
        elif code == "WV":
            m = re.fullmatch(r"(\d{3})(\d{2,3})(?:KT)?", val)
            add(part, f"wind from {m[1]}° at {int(m[2])} kt" if m else f"wind {val}")
        elif code in ("TB", "IC"):
            add(part, ("turbulence: " if code == "TB" else "icing: ") + _hazard(code, val))
        elif code == "RM":
            add(part, f"remarks: {val}")
        else:
            not_decoded.append(part)
    if not_decoded:
        out["not_decoded"] = not_decoded
    out["idents"] = list(dict.fromkeys(idents))
    out["note"] = "PIREP altitudes and cloud heights are MSL (hundreds of feet), unlike METAR/TAF AGL."
    return out


def _hazard(code: str, val: str) -> str:
    table = _TB_INT if code == "TB" else _IC_INT
    words = []
    toks = val.split()
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in _FREQ:
            words.append(_FREQ[t])
        elif t in table:
            words.append(table[t])
        elif code == "TB" and t in ("CAT", "CHOP", "LLWS"):
            words.append({"CAT": "clear air turbulence", "CHOP": "chop",
                          "LLWS": "low-level wind shear"}[t])
        elif code == "IC" and t in ("RIME", "CLR", "MX", "MXD"):
            words.append({"RIME": "rime", "CLR": "clear", "MX": "mixed", "MXD": "mixed"}[t])
        elif t in ("BLO", "ABV") and i + 1 < len(toks) and (a := _alt_range(f"{t} {toks[i + 1]}")):
            words.append(a)
            i += 1
        elif (a := _alt_range(t)):
            words.append(a)
        else:
            words.append(t)
        i += 1
    return " ".join(words)


# ---------------------------------------------------------------------------

def kind(text: str) -> str:
    t = text.strip().upper()
    if re.search(r"\b(UA|UUA)\s*/\s*OV\b", t) or re.match(r"^[A-Z0-9]{3,4}\s+UU?A\b", t):
        return "PIREP"
    if t.startswith("TAF") or re.search(r"\b\d{4}/\d{4}\b", t) and re.search(r"\bFM\d{6}\b|\bTEMPO\b|\bBECMG\b|\bPROB\d{2}\b", t):
        return "TAF"
    if re.search(r"\b\d{4}/\d{4}\b", t) and not re.search(r"\b[AQ]\d{4}\b", t):
        return "TAF"
    return "METAR"


def decode(text: str, at: str = "") -> dict:
    """Decode a METAR/SPECI, TAF or PIREP; `at` (TAFs) picks the forecast
    in effect at that time."""
    text = " ".join(text.strip().upper().replace("=", " ").split())
    k = kind(text)
    if k == "PIREP":
        out = decode_pirep(text)
    elif k == "TAF":
        out = decode_taf(text, at)
    else:
        out = decode_metar(text)
        out["rules"] = [CEILING_RULE, CATEGORY_RULE, VFR_RULE]
    if k == "TAF":
        out["rules"] = [CEILING_RULE, CATEGORY_RULE, VFR_RULE]
    return out
