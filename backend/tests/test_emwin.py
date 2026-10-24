from datetime import UTC, datetime
from pathlib import Path

from satweather.emwin import archived_path, normalize, parse_name

FIX = Path(__file__).parent / "fixtures" / "emwin"


def test_parse_text_name():
    n = parse_name("A_FXUS62KTBW232354_C_KWIN_20261023235426_080851-2-AFDTBWFL.txt")
    assert n.is_text and n.wmo == "FXUS62" and n.cccc == "KTBW" and n.ddhhmm == "232354"
    assert n.received == datetime(2026, 10, 23, 23, 54, 26, tzinfo=UTC)
    assert n.priority == 2 and n.pil == "AFDTBW" and n.nnn == "AFD"


def test_parse_graphic_name():
    n = parse_name("Z_QATA00KWBC231145_C_KWIN_20261023234530_080521-3-RADREFUS.GIF")
    assert not n.is_text and n.awips_long == "RADREFUS" and n.ext == "gif"


def test_rejects_other_names():
    assert parse_name("20261023230050-pac24_latestBW.lrit.gif") is None


def test_every_fixture_name_parses():
    names = [p.name for p in FIX.iterdir()]
    assert len(names) >= 80
    assert all(parse_name(n) for n in names), [n for n in names if not parse_name(n)]


def test_normalize_injects_missing_awips_line():
    f = next(FIX.glob("*-PAFFFNFJ.txt"))
    n = parse_name(f.name)
    lines = normalize(f.read_bytes(), n).split("\n")
    assert lines[0].startswith("FAFJ40 NFFN") and lines[1] == "PAFFFN"


def test_normalize_keeps_existing_awips_line():
    f = next(FIX.glob("*-SVREPZTX.txt"))
    lines = normalize(f.read_bytes(), parse_name(f.name)).split("\n")
    assert lines[1] == "SVREPZ" and "\r" not in "\n".join(lines)


def test_archived_path_uses_utc_mtime_date():
    t = datetime(2026, 10, 24, 0, 30, tzinfo=UTC).timestamp()
    assert str(archived_path("A_X.txt", t)) == "EMWIN/archive/2026-10-24/A_X.txt"
