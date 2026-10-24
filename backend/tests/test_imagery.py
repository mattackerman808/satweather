import shutil
from datetime import UTC, datetime
from pathlib import Path

from satweather.imagery import from_emwin, label, parse_path, read_projection
from satweather.imaging import media_rel

FIX = Path(__file__).parent / "fixtures" / "images"


def test_band_and_composite_names():
    b = parse_path("IMAGES/GOES-18/Full Disk/2026-10-24_03-00-22/G18_13_20261024T030022Z.png")
    assert (b.source, b.sector, b.product, b.kind, b.overlay) == ("GOES-18", "Full Disk", "13", "band", False)
    assert b.scan_time == datetime(2026, 10, 24, 3, 0, 22, tzinfo=UTC)
    r = parse_path("IMAGES/GOES-18/Mesoscale 1/2026-10-24_03-07-26/abi_rgb_ABI_False_Color_map.png")
    assert (r.sector, r.product, r.kind, r.overlay) == ("Mesoscale 1", "ABI_False_Color", "rgb", True)
    c = parse_path("IMAGES/GOES-18/Full Disk/2026-10-24_03-00-22/abi_rgb_Dirty_Longwave_Window_-_CIRA.png")
    assert (c.product, c.overlay) == ("Dirty_Longwave_Window_-_CIRA", False)


def test_himawari_has_no_sector_level():
    h = parse_path("IMAGES/Himawari/2026-10-24_02-51-00/ahi_rgb_AHI_False_Color_(WIP)_map.png")
    assert (h.source, h.sector, h.product, h.overlay) == ("Himawari", "Full Disk", "AHI_False_Color_(WIP)", True)
    assert parse_path("IMAGES/Himawari/2026-10-24_02-51-00/HIM_3_20261024T025100Z.png").product == "3"


def test_l2_composites_only():
    c = parse_path("L2/GOES-18/Full Disk/2026-10-23_22-40-22/abi_rgb_Total_Precipitable_Water_map.png")
    assert (c.source, c.sector, c.product, c.kind, c.overlay) == ("GOES-18", "L2", "Total_Precipitable_Water", "rgb", True)
    assert parse_path("L2/GOES-18/Full Disk/2026-10-23_22-40-22/G18_TPW_20261023T224022Z.png") is None


def test_lrit_charts():
    c = parse_path("IMAGES/NWS/20261024030050-pac48per_latestBW.lrit.gif")
    assert (c.source, c.sector, c.product, c.kind) == ("NWS", "Charts", "pac48per", "graphic")
    assert c.scan_time == datetime(2026, 10, 24, 3, 0, 50, tzinfo=UTC)
    assert parse_path("IMAGES/NWS/20261024010045-WATL_latest.lrit.gif").product == "WATL"


def test_emwin_graphics():
    t = datetime(2026, 10, 24, 3, 22, tzinfo=UTC)
    assert (from_emwin("RADPACSW", t).sector, from_emwin("IMGWWAUS", t).sector) == ("Radar", "Charts")
    assert label("NWS", "RADPACSW", "graphic") == "Pacific Southwest"
    assert label("NWS", "EP22YYWS", "graphic") == "EP22 surface wind field"
    assert label("NWS", "pac48per", "graphic") == "Tropical E Pacific 48 h wave period forecast"
    assert label("NWS", "XYZ123US", "graphic") == "XYZ123US"


def test_not_images():
    assert parse_path("IMAGES/NWS/readme.txt") is None
    assert parse_path("IMAGES/GOES-18/Full Disk/2026-10-24_03-00-22/product.cbor") is None
    assert parse_path("IMAGES/GOES-18/Full Disk/not-a-scan/G18_13_20261024T030022Z.png") is None


def test_labels():
    assert label("GOES-18", "13", "band") == "Band 13 · Clean longwave IR 10.3 µm"
    assert label("Himawari", "13", "band") == "Band 13 · Clean longwave IR 10.4 µm"
    assert label("GOES-18", "Dirty_Longwave_Window_-_CIRA", "rgb") == "Dirty Longwave Window - CIRA"


def test_projection_from_cbor(tmp_path):
    shutil.copy(FIX / "himawari_product.cbor", tmp_path / "product.cbor")
    p = read_projection(tmp_path)
    assert p["type"] == "geos" and p["lon0"] == 140.0 and p["width"] == 2200
    assert read_projection(tmp_path / "missing") is None


def test_media_paths_are_url_safe():
    t = datetime(2026, 10, 24, 2, 51, tzinfo=UTC)
    assert media_rel("Himawari", "Full Disk", t, "AHI_False_Color_(WIP)", True) == \
        "sat/himawari/full-disk/20261024T025100Z/ahi-false-color-wip_map"
