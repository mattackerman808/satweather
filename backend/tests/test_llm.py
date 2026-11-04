import json
from datetime import date
from pathlib import Path

import httpx
import pytest

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


def test_later_dates_and_validation():
    rows = [{"date": f"2026-11-{d:02d}"} for d in range(4, 11)]  # PFM covers Nov 4..10
    later = fl.later_dates(rows, date(2026, 11, 4))
    assert later == ["2026-11-11", "2026-11-12", "2026-11-13"]
    ok = {"headline": "h", "summary": "s", "trends": [], "changes": [],
          "beyond_day7": {"text": "t", "days": [{"date": d, "temp": "above", "precip": "near"} for d in later]}}
    v = fl.validate_outlook(later)
    assert v(ok) is None
    bad = json.loads(json.dumps(ok))
    bad["beyond_day7"]["days"][0]["temp"] = "hot"
    assert "invalid" in v(bad)
    bad["beyond_day7"]["days"] = bad["beyond_day7"]["days"][:2]
    assert "exactly" in v(bad)
    assert fl.validate_afd({"sections": [{"title": "Key messages", "text": "x"}], "glossary": []}) is None
    assert fl.validate_afd({"sections": []})


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
