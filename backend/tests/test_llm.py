import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from satweather import cpc
from satweather import forecast_llm as fl
from satweather import llm
from satweather.config import get_settings
from satweather.emwin import normalize, parse_name

FIX = Path(__file__).parent / "fixtures" / "emwin"


def _text(glob: str) -> str:
    f = next(FIX.glob(glob))
    return normalize(f.read_bytes(), parse_name(f.name))


def test_check_numbers():
    allowed = {64, 49, 57, 44}
    assert llm.check_numbers("Highs near 64 today, lows around 49 [PFM].", allowed) == []
    assert llm.check_numbers({"summary": "Warm, 64° then 57°"}, allowed) == []
    assert llm.check_numbers("A high of 66 on Friday.", allowed) == [66]
    assert llm.check_numbers("Lows in the 40s, 30% rain chance, 3 days", allowed) == []
    assert llm.numbers_in("high 64F; low 49F") == {64, 49}


def test_complete_request_shape(monkeypatch):
    monkeypatch.setenv("SATWEATHER_LLM_URL", "http://white:8000/")
    monkeypatch.setenv("SATWEATHER_LLM_API_KEY", "k")
    get_settings.cache_clear()
    seen = {}

    def post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, body=json, headers=headers)
        content = '```json\n{"headline": "ok"}\n```'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(llm.httpx, "post", post)
    try:
        assert llm.complete("sys", "user") == {"headline": "ok"}
    finally:
        get_settings.cache_clear()
    assert seen["url"] == "http://white:8000/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer k"
    body = seen["body"]
    assert body["model"] == "qwen3.8-27b" and body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["response_format"] == {"type": "json_object"} and body["messages"][1]["content"] == "user"
    assert "priority" not in body


def test_disabled_without_url(monkeypatch):
    monkeypatch.delenv("SATWEATHER_LLM_URL", raising=False)
    get_settings.cache_clear()
    try:
        assert not llm.enabled() and not llm.available()
    finally:
        get_settings.cache_clear()


def test_afd_sections_drop_aviation_and_marine():
    s = fl.afd_sections(_text("*-AFDAJKAK.TXT"))
    assert ".SYNOPSIS" in s and ".SHORT TERM" in s and ".LONG TERM" in s
    assert ".AVIATION" not in s and ".MARINE" not in s and "WATCHES/WARNINGS" not in s


def test_outlook_validation():
    ok = {"headline": "h", "summary": "s", "trends": [], "changes": [], "beyond_day7": "t"}
    assert fl.validate_outlook(ok) is None
    assert "invalid" in fl.validate_outlook({**ok, "trends": "x"})
    assert "invalid" in fl.validate_outlook({**ok, "beyond_day7": {"days": []}})
    assert fl.validate_afd({"sections": [{"title": "Key messages", "text": "x"}], "glossary": []}) is None
    assert fl.validate_afd({"sections": []})


def test_cpc_tables():
    raw = _text("*-PMDMRDMD.TXT")
    t = cpc.parse_tables(raw)
    assert (t["6-10"]["start"], t["6-10"]["end"]) == (date(2026, 11, 9), date(2026, 11, 13))
    assert (t["8-14"]["start"], t["8-14"]["end"]) == (date(2026, 11, 11), date(2026, 11, 17))
    assert len(t["6-10"]["rows"]) == 60
    assert t["6-10"]["rows"]["AK INT BSN"] == ("A", "A") and t["6-10"]["rows"]["ALABAMA"] == ("N", "B")
    assert cpc.later_dates("2026-11-10", date(2026, 11, 4)) == ["2026-11-11", "2026-11-12", "2026-11-13"]
    later = cpc.later(raw, cpc.region("IL", -87.9, 41.98), ["2026-11-13", "2026-11-16", "2026-11-30"])
    assert later == [{"date": "2026-11-13", "temp": "above", "precip": "below", "period": "6-10", "region": "Illinois"},
                     {"date": "2026-11-16", "temp": "above", "precip": "below", "period": "8-14", "region": "Illinois"}]
    assert cpc.region("MT", -112, 46.6) == "W MONTANA" and cpc.region("TX", -106.4, 31.8) == "W TEXAS"
    assert cpc.region("AK", -147.7, 64.8) == "AK INT BSN" and cpc.region("AK", -134.4, 58.3) == "AK PNHDL"
    assert cpc.region("HI", -157.9, 21.3) is None and cpc.later(raw, None, ["2026-11-13"]) == []


def test_daily_table():
    rows = [{"date": "2026-11-04", "hi": 64, "lo": 49,
             "day": {"condition": {"text": "Chance of rain"}, "pop": 60, "qpf": 0.02, "wind_dir": "S",
                     "wind_spd": 16, "wind_gust": 27},
             "night": {"condition": {"text": "Mostly cloudy"}, "pop": 20}}]
    assert fl.daily_table(rows) == ("Wed Nov 4; high 64F; low 49F; day: Chance of rain, rain chance 60%, 0.02 in, "
                                    "wind S 16 mph gusts 27; night: Mostly cloudy, rain chance 20%")


@pytest.mark.parametrize("raw,clean", [("```json\n{}\n```", "{}"), (" {} ", "{}")])
def test_strip_fence(raw, clean):
    assert llm._strip_fence(raw) == clean
