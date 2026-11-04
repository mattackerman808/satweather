import pytest

from satweather.api.zones import _split_state
from satweather.refdata import place_name


@pytest.mark.parametrize("raw,name", [
    ("Davis city", "Davis"), ("Boise City city", "Boise City"), ("Abanda CDP", "Abanda"),
    ("Athens-Clarke County unified government (balance)", "Athens-Clarke County"),
    ("Lexington-Fayette urban county", "Lexington-Fayette"), ("San Juan zona urbana", "San Juan"),
    ("Indianapolis city (balance)", "Indianapolis"),
])
def test_place_name(raw, name):
    assert place_name(raw) == name


@pytest.mark.parametrize("q,out", [
    ("davis, ca", ("davis", "CA")), ("davis ca", ("davis", "CA")), ("Davis, California", ("Davis", "CA")),
    ("charleston west virginia", ("charleston", "WV")), ("washington, dc", ("washington", "DC")),
    ("west virginia", ("west virginia", None)), ("new york", ("new york", None)),
    ("kansas city", ("kansas city", None)), ("davis", ("davis", None)), ("davis, zz", ("davis, zz", None)),
])
def test_split_state(q, out):
    assert _split_state(q) == out
