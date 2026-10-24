"""Airport importance classes for map decluttering — VENDORED from Tachbase.

Source: github.com/mattackerman808/tachbase-api,
        api/tachbase_api/services/weather.py @ 9d391c2 (_MAJOR_HUBS,
        _CLASS_B, _CLASS_C and the zoom tiers in get_stations).
The sets are copied unchanged; re-copy them when Tachbase's change.

Tachbase's tiers: zoom <=2 hubs, <=4 Class B, 5 B+C, 6 B+C+towered,
>=7 everything. satweather has no tower flag and covers far more than the
US (GOES-18's disk is mostly Pacific), so `tier()` adds aviationweather.gov's
global station priority (0 = most important ... 9) as the second test at
every tier: see satweather.api.stations.
"""

MAJOR_HUBS = {
    "KSEA", "KLAX", "KDEN", "KDFW", "KORD", "KATL", "KJFK", "PANC", "PHNL",
}
CLASS_B = {
    "KATL", "KBOS", "KBWI", "KCLT", "KDCA", "KDEN", "KDFW", "KDTW",
    "KEWR", "KFLL", "KHOU", "KIAH", "KJFK", "KLAS", "KLAX",
    "KLGA", "KMCO", "KMEM", "KMIA", "KMSP", "KORD", "KPHL", "KPHX",
    "KSAN", "KSEA", "KSFO", "KSLC", "KSTL", "KTPA", "PHNL",
    "TJSJ", "PGUM",
}
CLASS_C = {
    "KABQ", "KALB", "KAUS", "KBDL", "KBHM", "KBNA", "KBUF", "KBUR",
    "KCHS", "KCLE", "KCMH", "KCOS", "KCVG", "KDAY", "KDSM", "KELP",
    "KFNT", "KFSM", "KGEG", "KGFK", "KGRR", "KGSO", "KGSB", "KGSP",
    "KHSV", "KICT", "KIND", "KJAX", "KLIT", "KMCI", "KMDT", "KMKE",
    "KMSY", "KOKC", "KOMA", "KONT", "KORF", "KPBI", "KPDX", "KPIT",
    "KPNS", "KPVD", "KRDU", "KRIC", "KRNO", "KROC", "KRSW", "KSAT",
    "KSAV", "KSDF", "KSJC", "KSMF", "KSNA", "KSRQ", "KSYR", "KTUL",
    "KTUS", "KTYS",
    "PANC", "TJBQ", "PGSN", "PADK",
}


# Zoom -> (US class set allowed, max AWC priority, row limit). The limits
# are Tachbase's; None means no class filter (show everything).
def tier(zoom: float, large_bbox: bool) -> tuple[frozenset[str] | None, int, int]:
    z = int(zoom)
    if z <= 2:
        return frozenset(MAJOR_HUBS), 0, 50
    if z <= 4:
        return frozenset(CLASS_B), 1, 50
    if z == 5:
        return frozenset(CLASS_B | CLASS_C), 2, 100
    if z == 6:
        # Tachbase adds towered fields here; AWC priority 3 stands in.
        return frozenset(CLASS_B | CLASS_C), 3, 100
    if z <= 8:
        return None, 9, 200
    return None, 9, 25000 if large_bbox else 5000
