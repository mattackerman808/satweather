from datetime import UTC, datetime
from pathlib import Path

from satweather.emwin import normalize, parse_name
from satweather.tropical import _when, category, parse_tcm, parse_two

FIX = Path(__file__).parent / "fixtures" / "emwin"


def _text(glob: str) -> str:
    f = next(FIX.glob(glob))
    return normalize(f.read_bytes(), parse_name(f.name))


def test_tcm_vance_11():
    a = parse_tcm(_text("*-HEFZ22US.txt"))
    assert (a.storm_id, a.basin, a.number, a.kind, a.name) == ("EP222026", "EP", "11", "Hurricane", "Vance")
    assert a.issued == datetime(2026, 10, 24, 3, 0, tzinfo=UTC) and a.valid == a.issued
    assert (a.lat, a.lon, a.move_dir, a.move_kt) == (12.0, -102.6, 345, 6)
    assert (a.pressure_mb, a.wind_kt, a.gust_kt) == (965, 95, 115)
    assert a.radii == {"64": [30, 25, 20, 25], "50": [50, 40, 40, 40], "34": [130, 180, 80, 110]}
    assert a.prev == {"valid": datetime(2026, 10, 24, 0, 0, tzinfo=UTC), "lat": 11.7, "lon": -102.4}
    f = a.forecast
    assert [p.valid.strftime("%d/%H") for p in f] == ["24/12", "25/00", "25/12", "26/00", "26/12", "27/00", "28/00", "29/00"]
    assert (f[0].lat, f[0].lon, f[0].wind_kt, f[0].gust_kt, f[0].outlook) == (12.8, -102.8, 105, 130, False)
    assert f[0].radii["64"] == [35, 25, 20, 35]
    assert f[-1].outlook and f[-1].wind_kt == 70 and "64" not in f[-1].radii and f[-1].radii["34"] == [110, 100, 60, 80]
    assert not a.final


def test_tcm_statuses_and_month_end():
    text = _text("*-HEFZ22US.txt").replace("FORECAST VALID 27/0000Z 16.8N 104.2W",
                                          "FORECAST VALID 27/0000Z 16.8N 104.2W...POST-TROP/EXTRATROP")
    text = text.replace("OUTLOOK VALID 29/0000Z 18.4N 104.4W", "OUTLOOK VALID 29/0000Z...DISSIPATED")
    f = parse_tcm(text).forecast
    assert f[5].status == "POST-TROP/EXTRATROP" and f[5].lat == 16.8
    assert f[-1].status == "DISSIPATED" and f[-1].lat is None
    assert _when(datetime(2026, 10, 30, 21, tzinfo=UTC), 2, 0) == datetime(2026, 11, 2, 0, tzinfo=UTC)
    assert _when(datetime(2026, 11, 1, 3, tzinfo=UTC), 31, 18) == datetime(2026, 10, 31, 18, tzinfo=UTC)


def test_not_a_tcm():
    assert parse_tcm(_text("*-HEPZ32US.txt")) is None  # the public advisory has no forecast/advisory title


def test_categories():
    assert [category(w) for w in (30, 34, 64, 95, 96, 120, 140, None)] == ["TD", "TS", "1", "2", "3", "4", "5", "?"]


def test_outlooks():
    ep = parse_two(_text("A_ABPZ20KNHC240525*"))
    assert ep["none_expected"] is False and ep["active"].startswith("The National Hurricane Center is issuing")
    assert [(x["title"], x["chance_48h"], x["chance_7d"], x["level_7d"]) for x in ep["areas"]] == [
        ("Western Central Pacific", 10, 20, "low")]
    at = parse_two(_text("A_ABNT20KNHC240524*"))
    assert at == {"none_expected": True, "active": None, "areas": []}
