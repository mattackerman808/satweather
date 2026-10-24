"""Tachbase's decoder tests, vendored with satweather/wxdecode.py (same commit).

The MCP-server tests (those taking the `http`/`api` fixtures) are left
out; every decoder test is unchanged.
"""

from __future__ import annotations

import pytest

from satweather import wxdecode as w


def meanings(d: dict) -> dict[str, str]:
    return {x["group"]: x["meaning"] for x in d["decoded"] + d.get("remarks", [])}

@pytest.mark.parametrize("code,text", [
    ("+SN", "heavy snow"),
    ("-RA", "light rain"),
    ("RA", "moderate rain"),
    ("BLSNFG", "blowing snow and fog"),
    ("+BLSN", "heavy blowing snow"),
    ("VCTS", "thunderstorm in the vicinity (5-10 SM from the airport)"),
    ("VCSH", "showers in the vicinity (5-10 SM from the airport)"),
    ("TSRA", "thunderstorm with moderate rain"),
    ("+TSRAGR", "thunderstorm with heavy rain and hail"),
    ("-SHRA", "light rain showers"),
    ("FZRA", "moderate freezing rain"),
    ("FZFG", "freezing fog"),
    ("MIFG", "shallow fog"),
    ("BR", "mist"),
    ("TS", "thunderstorm"),
    ("+FC", "tornado or waterspout"),
])
def test_weather_codes(code, text):
    assert w.weather(code) == text

@pytest.mark.parametrize("code", ["KSFO", "A2992", "RMK", "SLP158", "BKN025"])
def test_not_weather(code):
    assert w.weather(code) is None

def test_vertical_visibility_is_an_indefinite_ceiling():
    """faa_atm_71: VV001 was read as "no ceiling"."""
    d = w.decode("METAR KHOT 181253Z AUTO 00000KT M1/4SM FG VV001 08/08 A3000 "
                 "RMK AO2 SLP158 T00780078")
    m = meanings(d)
    assert "indefinite ceiling at 100 ft AGL" in m["VV001"]
    assert m["M1/4SM"] == "visibility less than 1/4 statute mile"
    assert m["00000KT"] == "wind calm"
    assert m["SLP158"] == "sea-level pressure 1015.8 hPa"
    assert m["T00780078"] == "temperature 7.8°C, dew point 7.8°C (tenths)"
    assert d["summary"] == {"ceiling": "100 ft AGL", "visibility": "less than 1/4 SM",
                            "flight_category": "LIFR", "weather": ["fog"]}
    assert "not_decoded" not in d and any("1.1" in r for r in d["rules"])
    assert any("91.155" in r for r in d["rules"])

def test_begin_end_remark_shares_the_next_time():
    """faa_ira_15: RAESNB42 was timed as 1208Z."""
    d = w.decode("METAR KBNA 211250Z 33018KT 290V260 1/2SM R31/2700FT +SN BLSNFG VV008 "
                 "00/M03 A2991 RMK RAESNB42")
    m = meanings(d)
    assert m["RAESNB42"] == ("rain ended at 42 minutes past the hour; "
                             "snow began at 42 minutes past the hour")
    assert m["290V260"] == "wind direction varying between 290° and 260°"
    assert m["R31/2700FT"] == "runway 31 visual range 2,700 ft"
    assert m["00/M03"] == "temperature 0°C, dew point -3°C"
    assert d["summary"]["ceiling"] == "800 ft AGL"
    assert "not_decoded" not in d

@pytest.mark.parametrize("rmk,text", [
    ("RAB15E30", "rain began at 15 minutes past the hour; rain ended at 30 minutes past the hour"),
    ("TSB1159E1240", "thunderstorm began at 1159Z; thunderstorm ended at 1240Z"),
    ("FZRAB05", "freezing rain began at 5 minutes past the hour"),
])
def test_begin_end_remarks(rmk, text):
    assert meanings(w.decode(f"KXYZ 011200Z 00000KT 10SM CLR 10/05 A3000 RMK {rmk}"))[rmk] == text

def test_broken_layer_is_the_ceiling_and_mvfr():
    """faa_cax_23 needs the 2,500 ft ceiling."""
    d = w.decode("KHOB 151250Z 17006KT 4SM BKN025 OVC040 13/11 A2998")
    assert d["type"] == "METAR"
    assert d["summary"] == {"ceiling": "2,500 ft AGL", "visibility": "4 SM",
                            "flight_category": "MVFR"}

def test_few_and_scattered_are_not_ceilings():
    d = w.decode("KSJC 221753Z 31010KT 10SM FEW020 SCT250 21/09 A2995")
    assert d["summary"]["ceiling"].startswith("none")
    assert d["summary"]["flight_category"] == "VFR"

def test_mixed_fraction_visibility_and_gusts():
    d = w.decode("SPECI KSFO 221756Z 28012G20KT 1 1/2SM -RA BR FEW008 SCT015 OVC030 12/11 "
                 "A2990 RMK AO2 PK WND 29032/1738 WSHFT 1715 PRESRR P0012")
    m = meanings(d)
    assert d["type"] == "SPECI"
    assert m["28012G20KT"] == "wind from 280° true at 12 kt, gusting 20 kt"
    assert m["1 1/2SM"] == "visibility 1 1/2 statute miles"
    assert m["PK WND 29032/1738"] == "peak wind from 290° at 32 kt at 1738Z"
    assert m["WSHFT 1715"] == "wind shift at 1715Z"
    assert m["P0012"] == "0.12 in of precipitation in the past hour"
    assert d["summary"]["flight_category"] == "IFR"

def test_unknown_groups_are_listed_not_guessed():
    d = w.decode("KXYZ 011200Z 00000KT 10SM CLR 10/05 A3000 ZZTOP RMK QQQ")
    assert d["not_decoded"] == ["ZZTOP", "QQQ"]

@pytest.mark.parametrize("ceiling_ft,vis,cat", [
    (None, 10, "VFR"), (3100, 6, "VFR"), (3000, 10, "MVFR"), (5000, 5, "MVFR"),
    (999, 10, "IFR"), (5000, 2.5, "IFR"), (499, 10, "LIFR"), (5000, 0.75, "LIFR"),
])
def test_flight_category(ceiling_ft, vis, cat):
    assert w.flight_category(ceiling_ft, vis) == cat

KHOU_TAF = ("KHOU 151720Z 1518/1618 22009KT P6SM SCT030 SCT250 FM160000 18005KT P6SM BKN050 "
            "BKN120 FM160600 21007KT P6SM VCSH SCT025 BKN200 FM160900 34010KT P6SM VCTS "
            "BKN035CB BKN250 TEMPO 1611/1613 TSRA BKN012 OVC025CB FM161600 35007KT P6SM BKN020")

def test_taf_at_arrival_time():
    """faa_atm_70: the 0900Z arrival was called IFR with the TEMPO "right after"."""
    d = w.decode(KHOU_TAF, at="0900Z")
    assert d["type"] == "TAF" and "not_decoded" not in d
    at = d["at"]
    assert at["time"] == "160900Z"
    assert at["prevailing"]["group"] == "FM160900"
    assert "thunderstorm in the vicinity (5-10 SM from the airport)" in at["prevailing"]["decoded"]
    assert at["prevailing"]["summary"]["flight_category"] == "VFR"
    assert at["prevailing"]["summary"]["ceiling"] == "3,500 ft AGL"
    assert at["also_possible"] == []

def test_taf_tempo_window():
    at = w.decode(KHOU_TAF, at="161200Z")["at"]
    assert at["prevailing"]["group"] == "FM160900"
    assert [p["group"] for p in at["also_possible"]] == ["TEMPO 1611/1613"]
    assert at["also_possible"][0]["summary"]["ceiling"] == "1,200 ft AGL"

def test_taf_periods_and_bounds():
    d = w.decode("TAF " + KHOU_TAF)
    assert [p["group"] for p in d["periods"]] == [
        "1518/1618", "FM160000", "FM160600", "FM160900", "TEMPO 1611/1613", "FM161600"]
    assert w.decode(KHOU_TAF, at="1900Z")["at"]["prevailing"]["group"] == "1518/1618"
    assert "outside" in w.decode(KHOU_TAF, at="161900Z")["at"]["error"]
    assert "not understood" in w.decode(KHOU_TAF, at="noon")["at"]["error"]

def test_taf_prob_and_wind_shear():
    d = w.decode("TAF AMD KDEN 221120Z 2212/2318 27015G25KT P6SM SCT080 WS020/32045KT "
                 "PROB30 2220/2224 -TSRA BKN060CB BECMG 2302/2304 VRB03KT")
    groups = [p["group"] for p in d["periods"]]
    assert groups == ["2212/2318", "PROB30 2220/2224", "BECMG 2302/2304"]
    base = {x["group"]: x["meaning"] for x in d["periods"][0]["decoded"]}
    assert base["WS020/32045KT"] == "low-level wind shear at 2,000 ft AGL, wind from 320° at 45 kt"
    assert d["header"][0]["meaning"] == "amended forecast"

def test_taf_across_month_end():
    at = w.decode("KXYZ 301730Z 3018/0118 18005KT P6SM SKC FM010600 36010KT 3SM BR OVC008",
                  at="010700Z")["at"]
    assert at["prevailing"]["group"] == "FM010600"
    assert at["prevailing"]["summary"]["flight_category"] == "IFR"

def test_urgent_pirep_with_radial_location():
    """faa_ats_21: TYR was placed at Charlotte."""
    d = w.decode("TYR UUA/OV TYR180015/TM 1757/FL310/TP B737/TB MOD-SEV CAT 350-390")
    m = meanings(d)
    assert d["type"] == "PIREP" and d["idents"] == ["TYR"]
    assert "URGENT" in m["UUA"]
    assert m["OV TYR180015"] == "location: 15 NM south of TYR (its 180° radial)"
    assert m["FL310"] == "altitude: FL310 (31,000 ft)"
    assert m["TB MOD-SEV CAT 350-390"] == ("turbulence: moderate to severe clear air turbulence "
                                           "between 35,000 and 39,000 ft MSL")

def test_pirep_sky_layers_are_base_cover_top_msl():
    """faa_cax_22: '025 OVC 045' was read as 25% scattered."""
    d = w.decode("KMOB UA/OV 15NW MOB 1340Z/SK 025 OVC 045/075 OVC 080/090 OVC")
    m = meanings(d)
    assert m["SK 025 OVC 045/075 OVC 080/090 OVC"] == (
        "sky cover: 3 layers: overcast, base 2,500 ft MSL, top 4,500 ft MSL; overcast, base "
        "7,500 ft MSL, top 8,000 ft MSL; overcast, base 9,000 ft MSL")
    assert m["1340"] == "time: 1340Z"
    assert m["OV 15NW MOB 1340Z"] == "location: 15 NM NW of MOB"
    assert "routine" in m["UA"]

def test_pirep_other_fields():
    d = w.decode("DEN UA /OV DEN-COS/TM 1630/FL095/TP PA28/SK BKN065-TOP090/WX FV03SM HZ FU"
                 "/TA M02/WV 27045KT/IC LGT RIME 080-095/TB OCNL LGT-MOD CHOP BLO 090/RM CLIMBING")
    m = meanings(d)
    assert d["idents"] == ["DEN", "COS"] and "not_decoded" not in d
    assert m["OV DEN-COS"] == "location: along the route from DEN to COS"
    assert m["FL095"] == "altitude: 9,500 ft MSL"
    assert m["SK BKN065-TOP090"] == "sky cover: 1 layer: broken, base 6,500 ft MSL, top 9,000 ft MSL"
    assert m["WX FV03SM HZ FU"] == "weather: flight visibility 3 SM, haze, smoke"
    assert m["TA M02"] == "outside air temperature -2°C"
    assert m["WV 27045KT"] == "wind from 270° at 45 kt"
    assert m["IC LGT RIME 080-095"] == "icing: light rime between 8,000 and 9,500 ft MSL"
    assert m["TB OCNL LGT-MOD CHOP BLO 090"] == "turbulence: occasional light to moderate chop below 9,000 ft MSL"

@pytest.mark.parametrize("text,kind", [
    ("KSFO 221756Z 28012KT 10SM CLR 20/10 A3000", "METAR"),
    ("METAR KSFO 221756Z 28012KT 10SM CLR 20/10 A3000", "METAR"),
    ("TAF KSFO 221720Z 2218/2324 28012KT P6SM SKC", "TAF"),
    ("KSFO 221720Z 2218/2324 28012KT P6SM SKC FM230000 VRB03KT P6SM SKC", "TAF"),
    ("SFO UA /OV SFO/TM 1800/FL050/TP C172/TB LGT", "PIREP"),
])
def test_kind(text, kind):
    assert w.kind(text) == kind

@pytest.mark.parametrize("group,text", [
    ("BKN035CB", "broken cumulonimbus (5/8-7/8), base 3,500 ft AGL (METAR/TAF heights are cloud "
                 "bases; tops aren't reported)"),
    ("SCT030TCU", "scattered towering cumulus (3/8-4/8), base 3,000 ft AGL (METAR/TAF heights "
                  "are cloud bases; tops aren't reported)"),
    ("OVC040", "overcast (8/8), base 4,000 ft AGL"),
])
def test_sky_heights_are_bases(group, text):
    """faa_atm_70: "BKN035CB" came back as "CB tops at 3,500 ft"."""
    assert w.sky_group(group)["meaning"] == text
