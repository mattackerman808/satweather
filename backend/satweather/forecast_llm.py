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
from datetime import date

# Part of every cache key: bump it when the prompts change, so cached
# answers written under the old prompt are regenerated.
PROMPT_VERSION = 2

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
Keep the products' strength of wording: breezy is not "strong winds", a chance of showers is not "heavy rain",
37 degrees is not "near freezing". Prefer day names ("Thursday") to "tonight" or "tomorrow".
Explain jargon in everyday words (no "shortwave", "CAA", "PWATs", "QPF" - say what they mean).
End EVERY sentence and list item with the products it came from, in square brackets:
[PFM] the point forecast table, [ZFP] the zone forecast, [AFD] the local forecasters' discussion,
[WPC] the national 3-7 day discussion, [CPC] the 6-10/8-14 day outlook, [CHG] the changes since the previous forecast.
Answer with one JSON object and nothing else, shaped like this example:
{
 "headline": "Rain on and off through Thursday, then a dry, cooler weekend. [ZFP]",
 "summary": "Showers are likely Wednesday with highs in the mid 60s. [ZFP] Thursday turns cooler, highs near 57. [PFM] The weekend stays dry with highs in the 50s. [ZFP]",
 "trends": ["Cooling from 64 on Wednesday to 52 by Friday. [PFM]"],
 "changes": ["Thursday's high was lowered 6 degrees since the last forecast. [CHG]"],
 "beyond_day7": "The outlook for the days after that favors warmer and drier than normal here. [CPC]",
 "confidence": "Forecasters are fairly confident about the dry weekend. [AFD]"
}
Use 1-4 trends, 0-3 changes (only from the CHANGES list; empty if it says none), 3-5 summary sentences.
For beyond_day7, say what the CPC TABLE line says for this place (or that there is none), adding detail from the CPC
discussion only where it names this region."""

_TILT_WORDS = {"above": "above normal", "near": "near normal", "below": "below normal"}


def outlook_prompt(place: str, office: str | None, zfp: str | None, table: str, changes: list[dict],
                   afd: str | None, wpc: str | None, cpc: str | None, later: list[dict]) -> str:
    chg = "\n".join(f"- {c['date']}: {c['text']}" for c in changes) or "(none)"
    tilt = "\n".join(f"- {d['date']}: temperature {_TILT_WORDS[d['temp']]}, precipitation {_TILT_WORDS[d['precip']]}"
                      f" ({d['region']}, {d['period']} day table)" for d in later) or "(none for this place)"
    parts = [
        f"PLACE: {place}" + (f" (NWS office {office})" if office else ""),
        "POINT FORECAST TABLE [PFM]:\n" + (table or "(none)"),
        "CHANGES SINCE THE PREVIOUS POINT FORECAST [CHG]:\n" + chg,
        "ZONE FORECAST [ZFP]:\n" + (zfp or "(none)"),
        "LOCAL FORECASTERS' DISCUSSION [AFD]:\n" + (afd or "(none)"),
        "NATIONAL 3-7 DAY DISCUSSION [WPC]:\n" + (wpc or "(none)"),
        "CPC TABLE FOR THIS PLACE, DAYS 8-10 [CPC]:\n" + tilt,
        "6-10 AND 8-14 DAY OUTLOOK DISCUSSION [CPC]:\n" + (cpc or "(none)"),
    ]
    return "\n\n".join(parts)


def validate_outlook(c: dict) -> str | None:
    if not isinstance(c, dict) or not isinstance(c.get("summary"), str) or not c.get("headline"):
        return "invalid: missing headline/summary"
    for k in ("trends", "changes"):
        if not isinstance(c.get(k, []), list):
            return f"invalid: {k} is not a list"
    if not isinstance(c.get("beyond_day7", ""), str):
        return "invalid: beyond_day7 is not a string"
    return None


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
