"""Prompts for the Forecast tab's LLM pieces (run through llm.cached).

The outlook: a plain-English 10-day story for one place, written only from
the products we pass (the zone's ZFP, the nearest PFM point as a daily
table, the changes since the previous PFM as computed by pfm.diff, the
office's AFD, and WPC's/CPC's national discussions). Days 8-10 come only
from CPC's 6-10/8-14 day outlook, as warmer/cooler, wetter/drier odds.

The plain AFD: the office's discussion rewritten for non-meteorologists,
with a glossary of the terms it used.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

TILTS = {"above", "near", "below", "unknown"}

_SECTION = re.compile(r"^\.([A-Z][A-Z0-9 /&-]*?)(?:\s*/[^\n]*?/)?\.\.\.", re.M)
_KEEP = ("KEY MESSAGES", "SYNOPSIS", "SHORT TERM", "LONG TERM", "DISCUSSION", "UPDATE", "NEAR TERM",
         "WHAT HAS CHANGED", "FORECAST", "OVERVIEW")


def afd_sections(raw: str) -> str:
    """The forecast reasoning of an AFD: key messages, synopsis, short and
    long term. Aviation, marine, fire weather, climate and the
    watch/warning list are dropped (they don't change a place's outlook)."""
    ms = list(_SECTION.finditer(raw))
    if not ms:
        return raw
    keep = []
    for i, m in enumerate(ms):
        title = m.group(1).strip()
        if any(title.startswith(k) for k in _KEEP):
            end = ms[i + 1].start() if i + 1 < len(ms) else len(raw)
            keep.append(raw[m.start():end].replace("&&", "").strip())
    return "\n\n".join(keep) or raw


def daily_table(rows: list[dict]) -> str:
    """pfm.daily() rows as compact lines for the prompt."""
    out = []
    for r in rows:
        d = date.fromisoformat(r["date"])
        parts = [d.strftime("%a %b %-d")]
        if r.get("hi") is not None:
            parts.append(f"high {r['hi']}F")
        if r.get("lo") is not None:
            parts.append(f"low {r['lo']}F")
        for half, label in (("day", "day"), ("night", "night")):
            h = r.get(half)
            if not h:
                continue
            bits = [h["condition"]["text"]]
            if h.get("pop") is not None:
                bits.append(f"rain chance {h['pop']}%")
            if h.get("qpf"):
                bits.append(f"{h['qpf']} in")
            if h.get("wind_spd") is not None:
                w = f"wind {h.get('wind_dir') or ''} {h['wind_spd']} mph".replace("  ", " ")
                if h.get("wind_gust"):
                    w += f" gusts {h['wind_gust']}"
                bits.append(w)
            parts.append(f"{label}: " + ", ".join(bits))
        out.append("; ".join(parts))
    return "\n".join(out)


OUTLOOK_SYSTEM = """You write the plain-English outlook on a public weather website, for people who are not meteorologists.
You are given National Weather Service products for one place. Write ONLY from them: never add weather, numbers,
places or certainty that the products don't contain. Every temperature you mention must appear in the products.
Explain jargon in everyday words (no "shortwave", "CAA", "PWATs", "QPF" - say what they mean).
Tag each sentence with the products it came from, in square brackets at its end: [PFM] the point forecast table,
[ZFP] the zone forecast, [AFD] the local forecasters' discussion, [WPC] the national 3-7 day discussion,
[CPC] the 6-10/8-14 day outlook discussion, [CHG] the list of changes since the previous forecast.
Answer with one JSON object and nothing else:
{
 "headline": "one short sentence: the most important thing about the coming week",
 "summary": "3-5 sentences: how the week unfolds, in order",
 "trends": ["1-4 short items: warming or cooling trends, the wettest or windiest stretch, a pattern change"],
 "changes": ["0-3 short items putting the provided changes into words; empty if none were provided"],
 "beyond_day7": {"text": "1-2 sentences on the given later dates from the CPC discussion for this region, as odds (warmer/cooler, wetter/drier than normal); say so if the discussion doesn't cover this region",
                 "days": [{"date": "YYYY-MM-DD", "temp": "above|near|below|unknown", "precip": "above|near|below|unknown"}]},
 "confidence": "one sentence on how sure the forecasters are, from the AFD/WPC wording"
}"""


def outlook_prompt(place: str, office: str | None, zfp: str | None, table: str, changes: list[dict],
                   afd: str | None, wpc: str | None, cpc: str | None, later: list[str]) -> str:
    chg = "\n".join(f"- {c['date']}: {c['text']}" for c in changes) or "(none)"
    parts = [
        f"PLACE: {place}" + (f" (NWS office {office})" if office else ""),
        f"LATER DATES for beyond_day7.days (exactly these, in order): {', '.join(later)}",
        "POINT FORECAST TABLE [PFM]:\n" + (table or "(none)"),
        "CHANGES SINCE THE PREVIOUS POINT FORECAST [CHG]:\n" + chg,
        "ZONE FORECAST [ZFP]:\n" + (zfp or "(none)"),
        "LOCAL FORECASTERS' DISCUSSION [AFD]:\n" + (afd or "(none)"),
        "NATIONAL 3-7 DAY DISCUSSION [WPC]:\n" + (wpc or "(none)"),
        "6-10 AND 8-14 DAY OUTLOOK DISCUSSION [CPC]:\n" + (cpc or "(none)"),
    ]
    return "\n\n".join(parts)


def later_dates(rows: list[dict], today: date) -> list[str]:
    """Days 8-10 counting today as day 1, past the PFM's last day."""
    last = max((date.fromisoformat(r["date"]) for r in rows), default=today)
    out = []
    for n in (7, 8, 9):
        d = today + timedelta(days=n)
        if d > last:
            out.append(d.isoformat())
    return out


def validate_outlook(later: list[str]):
    def check(c: dict) -> str | None:
        if not isinstance(c, dict) or not isinstance(c.get("summary"), str) or not c.get("headline"):
            return "invalid: missing headline/summary"
        for k in ("trends", "changes"):
            if not isinstance(c.get(k, []), list):
                return f"invalid: {k} is not a list"
        b = c.get("beyond_day7") or {}
        days = b.get("days") or []
        if later:
            if [d.get("date") for d in days if isinstance(d, dict)] != later:
                return f"invalid: beyond_day7.days must be exactly {later}"
            if any(d.get("temp") not in TILTS or d.get("precip") not in TILTS for d in days):
                return "invalid: temp/precip must be above, near, below or unknown"
        return None
    return check


AFD_SYSTEM = """You rewrite a National Weather Service Area Forecast Discussion (forecasters' technical notes) into plain
English for the public. Keep every forecast point, its timing and its uncertainty; add nothing that isn't in it;
keep every number exactly as written. Replace jargon with everyday words, and list the technical terms the original
uses in a short glossary. Keep the original's section order and give each section a plain title
(e.g. "Key messages", "Next couple of days", "Later this week", "For pilots", "On the water").
Answer with one JSON object and nothing else:
{"sections": [{"title": "...", "text": "..."}], "glossary": [{"term": "...", "meaning": "..."}]}"""


def validate_afd(c: dict) -> str | None:
    if not isinstance(c, dict) or not isinstance(c.get("sections"), list) or not c["sections"]:
        return "invalid: no sections"
    if any(not isinstance(s, dict) or not s.get("text") for s in c["sections"]):
        return "invalid: empty section"
    return None
