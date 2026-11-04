from datetime import UTC, datetime
from pathlib import Path

from satweather.emwin import normalize, parse_name
from satweather.pfm import condition, daily, diff, hourly, jsonable, parse_pfm, product_offset

FIX = Path(__file__).parent / "fixtures" / "emwin"


def _pfm(glob: str, issued: datetime):
    f = next(FIX.glob(glob))
    return parse_pfm(normalize(f.read_bytes(), parse_name(f.name)), issued)


LOT = datetime(2026, 11, 4, 6, 15, tzinfo=UTC)


def _ohare():
    return next(p for p in _pfm("*-PFMLOTIL.TXT", LOT) if "O'Hare" in p.name)


def test_point_header():
    p = _ohare()
    assert (p.ugc, p.lat, p.lon, p.elev_ft, p.utc_offset_h) == ("ILZ104", 41.99, -87.91, 667, -6)
    assert p.name == "Chicago O'Hare International Airport-Cook IL"
    assert p.expires == datetime(2026, 11, 4, 22, 0, tzinfo=UTC)


def test_times_and_values():
    p = _ohare()
    s = p.series
    # First 3-hourly column is 03 CST Wed = 09Z; the 6-hourly block follows.
    assert s[0]["t"] == datetime(2026, 11, 4, 9, 0, tzinfo=UTC) and s[0]["step"] == 3
    assert (s[0]["temp"], s[0]["dewpt"], s[0]["rh"], s[0]["wdir"], s[0]["wspd"], s[0]["wgust"]) == (64, 61, 90, "S", 14, 25)
    assert s[0]["clouds"] == "OV" and s[0]["wx"] == {"rain": "D"}
    assert all(b["t"] > a["t"] for a, b in zip(s, s[1:]))
    six = [e for e in s if e["step"] == 6]
    assert six[0]["t"] == datetime(2026, 11, 7, 6, 0, tzinfo=UTC) and six[0]["temp"] == 44
    assert six[-1]["t"] == datetime(2026, 11, 11, 0, 0, tzinfo=UTC)
    # Packed RH (`96100100`) reads as three cells.
    assert [e["rh"] for e in s[15:19]] == [96, 100, 100, 83]


def test_twelve_hour_periods():
    p = _ohare()
    hi = [(x["t"].strftime("%d %H"), x["max"]) for x in p.twelve if "max" in x]
    lo = [(x["t"].strftime("%d %H"), x["min"]) for x in p.twelve if "min" in x]
    assert hi[:3] == [("05 00", 64), ("06 00", 57), ("07 00", 52)]
    assert lo[:3] == [("05 12", 49), ("06 12", 44), ("07 12", 39)]
    first = p.twelve[0]
    assert (first["pop"], first["qpf"], first["snow"]) == (60, 0.02, "00-00")


def test_daily():
    rows = daily(_ohare())
    by = {r["date"]: r for r in rows}
    assert [(d, by[d]["hi"], by[d]["lo"]) for d in ("2026-11-04", "2026-11-05", "2026-11-07", "2026-11-09")] == \
        [("2026-11-04", 64, 49), ("2026-11-05", 57, 44), ("2026-11-07", 52, 37), ("2026-11-09", 60, 43)]
    wed = by["2026-11-04"]
    assert wed["pop"] == 60 and wed["day"]["wind_dir"] == "S" and wed["day"]["wind_gust"] == 27
    assert wed["day"]["condition"]["icon"] in ("rain", "showers")
    # The night after the last 6-hourly cell is not invented from one cell.
    assert by["2026-11-10"]["lo"] is None
    assert jsonable(rows)  # serialisable for the API


def test_hourly():
    h = hourly(_ohare(), "2026-11-04")
    assert [x["local"] for x in h][:3] == ["03:00", "06:00", "09:00"]
    assert h[0]["temp"] == 64 and h[0]["condition"]["text"]


def test_condition_rules():
    day = {"is_day": True}
    assert condition({**day, "clouds": "CL"})["text"] == "Sunny"
    assert condition({"is_day": False, "clouds": "CL"}) == {"icon": "clear", "text": "Clear", "night": True, "badge": None}
    assert condition({**day, "clouds": "SC"})["icon"] == "partly-cloudy"
    assert condition({**day, "clouds": "OV", "wx": {"rain": 2}, "pop": 40})["text"] == "Chance of rain"
    assert condition({**day, "clouds": "OV", "wx": {"showers": 3}})["text"] == "Showers likely"
    # Most likely wins; thunder only on a tie.
    assert condition({**day, "wx": {"rain": 3, "tstms": 2}})["text"] == "Rain likely"
    assert condition({**day, "wx": {"rain": 2, "tstms": 2}})["icon"] == "tstorms"
    # A slight chance under 30% stays a badge on the sky icon.
    c = condition({**day, "clouds": "SC", "wx": {"showers": 1}, "pop": 20})
    assert c["icon"] == "partly-cloudy" and c["badge"] == "20%"
    assert condition({**day, "clouds": "FW", "wind_spd": 28})["icon"] == "windy"
    assert condition({**day, "clouds": "FW", "wind_char": "BZ"})["text"] == "Mostly sunny and breezy"
    assert condition({**day, "wx": {"mix": 3}})["text"] == "Wintry mix likely"


def test_diff():
    cur = jsonable(_ohare().as_row())
    prev = jsonable(_ohare().as_row())
    thu = next(x for x in prev["twelve"] if x["t"].startswith("2026-11-06T00"))  # Thursday's high
    assert thu["max"] == 57
    thu["max"] = 63
    for e in prev["series"]:
        if e["t"].startswith("2026-11-05T15"):
            e["wx"] = {}
    out = diff(prev, cur)
    hi = [c for c in out if c["field"] == "hi"]
    assert hi == [{"date": "2026-11-05", "field": "hi", "from": 63, "to": 57, "text": "high down 6° (63° → 57°)"}]
    assert diff(cur, cur) == []


def test_reception_damage_and_variants():
    # SGX: a UTC row that lost its spacing (`UTC 3hrly21 00 03`).
    sgx = _pfm("*-PFMSGXCA.TXT", datetime(2026, 11, 3, 18, 41, tzinfo=UTC))
    march = next(p for p in sgx if p.name.startswith("March Air Force Base"))
    assert march.series[0]["t"] == datetime(2026, 11, 3, 21, 0, tzinfo=UTC) and march.series[0]["temp"] == 71
    # BUF: a Date row glued onto the line before it; the 6-hourly block still parses.
    buf = _pfm("*-PFMBUFNY.TXT", datetime(2026, 11, 4, 6, 32, tzinfo=UTC))
    newark = next(p for p in buf if p.name.startswith("Newark"))
    assert any(e["step"] == 6 for e in newark.series)
    # HFO: no elevation, UTC-10 (not +14).
    hfo = _pfm("*-PFMHFOHI.TXT", datetime(2026, 11, 4, 1, 12, tzinfo=UTC))
    assert hfo and hfo[0].elev_ft is None and {p.utc_offset_h for p in hfo} == {-10}
    # Guam: all caps, east longitude, and a LOCAL row labelled GMT.
    gum = _pfm("*-PFMPQ1GU.TXT", datetime(2026, 11, 4, 4, 40, tzinfo=UTC))
    pohnpei = gum[0]
    assert pohnpei.lon == 158.21 and pohnpei.utc_offset_h == 10
    assert pohnpei.series[0]["t"] == datetime(2026, 11, 4, 5, 0, tzinfo=UTC)
    assert daily(pohnpei)[1]["hi"] == 88
    # San Juan: AST.
    sju = _pfm("*-PFMSJUPR.TXT", datetime(2026, 11, 3, 18, 32, tzinfo=UTC))
    assert sju and {p.utc_offset_h for p in sju} == {-4}


def test_never_raises_on_garbage():
    assert parse_pfm("", LOT) == []
    assert parse_pfm("ILZ104-042200-\nsomewhere\n41.99N 87.91W\nDate\nCST 3hrly 99 98\n$$", LOT) == []
    assert product_offset("nothing here") is None


def test_run_together_cells_are_dropped():
    """Reception damage ran Thursday's high and Friday's low together
    (`7143`): that cell is dropped, and the day's high falls back to the
    hourly temperatures, not 7143."""
    f = next(FIX.glob("*-PFMLOTIL.TXT"))
    raw = normalize(f.read_bytes(), parse_name(f.name))
    seg = raw.index("Chicago O'Hare")
    row = raw.index("Max/Min", seg)
    end = raw.index("\n", row)
    good = raw[row:end]
    assert good.rstrip().endswith("57          44          52")
    bad = good.rstrip()[: -len("44          52")] + "4452"
    pt = next(p for p in parse_pfm(raw[:row] + bad + raw[end:], LOT) if "O'Hare" in p.name)
    assert all(v < 200 for x in pt.twelve for k, v in x.items() if k in ("max", "min"))
    days = {r["date"]: r for r in daily(pt)}
    assert days["2026-11-04"]["hi"] == 64 and days["2026-11-05"]["hi"] == 57
