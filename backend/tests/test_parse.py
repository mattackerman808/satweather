from datetime import UTC, datetime
from pathlib import Path

import pytest

from satweather.emwin import normalize, parse_name
from satweather.parse import parse

FIX = Path(__file__).parent / "fixtures" / "emwin"
NOW = datetime(2026, 10, 24, 1, 0, tzinfo=UTC)

# Products pyiem cannot parse today; they must still come back (raw).
KNOWN_RAW = {"EPH", "WWA", "TAF"}


def _parse(glob: str):
    f = next(FIX.glob(glob))
    n = parse_name(f.name)
    return parse(normalize(f.read_bytes(), n), n, NOW)


def test_all_fixtures_parse_or_fall_back():
    failures = {}
    for f in FIX.iterdir():
        n = parse_name(f.name)
        p = parse(normalize(f.read_bytes(), n), n, NOW)
        assert p.title and p.category
        if p.error:
            failures[n.nnn] = p.error
    assert set(failures) <= KNOWN_RAW, failures


def test_severe_thunderstorm_warning():
    p = _parse("*-SVREPZTX.txt")
    assert (p.nnn, p.category, p.office) == ("SVR", "warning", "KEPZ")
    assert p.title == "Severe Thunderstorm Warning"
    seg = p.segments[0]
    assert seg.ugcs == ["NMC013"] and seg.polygon_wkt.startswith("POLYGON")
    v = seg.vtec[0]
    assert (v.action, v.phenomena, v.significance, v.etn, v.year) == ("NEW", "SV", "W", 187, 2026)
    assert seg.motion["dir"] == 198 and seg.tags["hail"] == "1.00"


def test_zone_forecast_segments():
    p = _parse("*-ZFP*")
    assert p.category == "forecast" and len(p.segments) > 3
    assert all(s.ugcs for s in p.segments[:-1])


def test_metar_collective():
    p = _parse("*-SAHOURLY.txt")
    assert p.nnn == "SAH" and p.category == "observation"
    assert len(p.metars) > 3 and all(len(m.station) == 4 for m in p.metars)


def test_tropical_outlook_takes_afos_from_text():
    p = _parse("*-HAW*")
    assert p.nnn == "TWO" and p.category == "tropical"


@pytest.mark.parametrize("glob", ["*-EPH*"])
def test_unparseable_kept_raw(glob):
    p = _parse(glob)
    assert p.error and not p.segments


def test_taf_collective_title_is_catalog_name():
    assert _parse("A_FTUS*TAF*").title == "Terminal Aerodrome Forecast"


def test_specialist_parser_misfits_fall_back_to_generic():
    """pyiem sends every SWO/FFG to its MCD parser; the day 1 outlook and
    plain flash flood guidance have no LAT...LON and must still parse."""
    for glob in ("*-SWODY1US.txt", "*-FFGSJUGA.txt"):
        p = _parse(glob)
        assert p.error is None, (glob, p.error)
    assert _parse("*-SWODY1US.txt").title == "Day 1 Convective Outlook"
