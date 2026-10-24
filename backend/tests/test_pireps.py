import math
from datetime import UTC, datetime

from satweather.pireps import Spec, declination, destination, locate, midpoint, parse_ov
from satweather.reports import extract

T = datetime(2026, 10, 24, 2, 0, tzinfo=UTC)


def test_ov_forms():
    assert parse_ov("BIH UA /OV FIM305020/TM 0153/FL220/TP B738") == (
        [Spec(ident="FIM", bearing=305, magnetic=True, dist_nm=20)], False)
    assert parse_ov("PRB UA /OV KPRB360020 /TM 0105") == ([Spec(ident="KPRB", bearing=0, magnetic=True, dist_nm=20)], False)
    assert parse_ov("FAT UA /OV 15 SW FAT/TM 0100") == ([Spec(ident="FAT", bearing=225, dist_nm=15)], False)
    assert parse_ov("SAN UA /OV JLI/TM 0145") == ([Spec(ident="JLI")], False)
    assert parse_ov("SAN UA /OV SAN-LAX/TM 0145")[1] is True  # a route is only approximate
    specs, approx = parse_ov("XXX UA /OV 3330N 11800W/TM 0145")
    assert approx is False and math.isclose(specs[0].lat, 33.5) and math.isclose(specs[0].lon, -118.0)
    assert parse_ov("TYR UUA/OV TYR180015 1757/FL310")[0][0].ident == "TYR"  # time left in the field
    assert parse_ov("ONT UA /OV 5 NORTH HDF/TM 0003") == ([Spec(ident="HDF", bearing=0, dist_nm=5)], False)
    assert parse_ov("ONT UA /OV HDF VOR/TM 0000") == ([Spec(ident="HDF")], False)
    assert parse_ov("ABC UA /TM 0100/FL100") is None and parse_ov("ABC UA /OV SOMEWHERE OVER LAKE/TM 0100") is None


def test_great_circle():
    lat, lon = destination(34.0, -118.0, 90, 60)  # 60 NM due east, about 1.21 degrees of longitude here
    assert math.isclose(lat, 34.0, abs_tol=0.01) and math.isclose(lon, -118.0 + 60 / (60 * math.cos(math.radians(34))), abs_tol=0.01)
    assert destination(34.0, -118.0, 123, 0) == (34.0, -118.0)
    m = midpoint((32.73, -117.19), (33.94, -118.41))  # SAN-LAX
    assert math.isclose(m[0], 33.335, abs_tol=0.01) and math.isclose(m[1], -117.80, abs_tol=0.02)


def test_radials_are_magnetic():
    d = declination(34.357, -118.881, T)
    assert 10 < d < 13  # southern California, 2026: about 11.5 E
    # FIM 305 radial magnetic is about 316.5 true.
    lat, lon = locate([Spec(ident="FIM", bearing=305, magnetic=True, dist_nm=20)], {"FIM": (34.357, -118.881)}, T)
    true = math.degrees(math.atan2(lon - -118.881, (lat - 34.357) / math.cos(math.radians(34.4))) ) % 360
    assert math.isclose(true, 305 + d, abs_tol=0.5)
    assert locate([Spec(ident="NOPE")], {}, T) is None


def test_negative_reports_are_not_hazards():
    def hz(ov):
        product = f"UBUS01 KWBC 240100\nUABWBC\nPRB UA /OV KPRB360020 /TM 0105 /FL140 /TP C210 {ov}=\n"
        return extract("UAB", product, T)[0].hazards
    assert hz("/TB NEG /RM GOOD RIDE") == []
    assert hz("/TB SMTH") == []
    assert hz("/IC NEGCLR") == []
    assert hz("/TB LGT-MOD 200-220") == ["TB"]
    assert hz("/IC LGT RIME") == ["IC"]
    assert hz("/TB LIGHT") == ["TB"] and hz("/IC LT RIME") == ["IC"] and hz("/IC MOD CLEAR ICE") == ["IC"]
