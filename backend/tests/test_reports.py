from datetime import UTC, datetime
from pathlib import Path

from satweather.emwin import normalize, parse_name
from satweather.reports import ddhh, extract, metar

FIX = Path(__file__).parent / "fixtures" / "emwin"


def _extract(glob: str):
    f = next(FIX.glob(glob))
    n = parse_name(f.name)
    return extract(n.nnn, normalize(f.read_bytes(), n), n.received)


def test_us_taf_collective_splits_and_decodes():
    tafs = _extract("A_FTUS*TAF*")
    assert len(tafs) > 10 and all(t.kind == "TAF" and t.station for t in tafs)
    brl = next(t for t in tafs if t.station == "KBRL")
    assert brl.flight_category == "VFR" and not brl.decoded.get("not_decoded")
    assert brl.valid_from == datetime(2026, 10, 24, 0, tzinfo=UTC)
    # "2424" is hour 24 of the 24th: midnight on the 25th.
    assert brl.valid_to == datetime(2026, 10, 25, 0, tzinfo=UTC)


def test_international_taf_decodes_what_it_can():
    (t,) = _extract("*-TAFU31AU*")
    assert t.station == "YPDN" and t.decoded["periods"]
    assert any("INTER" in x for x in t.decoded.get("not_decoded", []))


def test_pireps_split_with_hazards():
    ps = _extract("*-UABA90CA*")
    assert [p.station for p in ps] == ["BFL", "SCK"]
    assert ps[0].hazards == ["IC"] and ps[1].hazards == ["TB"]
    assert ps[0].issued == datetime(2026, 10, 23, 23, 4, tzinfo=UTC)


def test_metar_decode_flight_category():
    d, cat = metar("KSFO 240056Z 29012G18KT 10SM FEW008 BKN012 17/12 A2998 RMK AO2")
    assert cat == "MVFR" and d["summary"]["ceiling"] == "1,200 ft AGL"


def test_ddhh_crosses_month_end():
    ref = datetime(2026, 10, 31, 22, tzinfo=UTC)
    assert ddhh(1, 6, 0, ref) == datetime(2026, 11, 1, 6, tzinfo=UTC)
    assert ddhh(31, 24, 0, ref) == datetime(2026, 11, 1, 0, tzinfo=UTC)
