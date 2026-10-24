"""EMWIN files as satdump writes them, and how to turn one into a product.

Filenames follow the WMO/EMWIN convention:

    A_FXUS62KTBW232354_C_KWIN_20261023235426_080851-2-AFDTBWFL.txt
    │ │     │   │       │      │              │      │ └ AWIPS id + 2-char region
    │ │     │   │       │      │              │      └ priority (1 = warning)
    │ │     │   │       │      │              └ EMWIN sequence number
    │ │     │   │       │      └ received at KWIN, UTC (YYYYMMDDhhmmss)
    │ │     │   │       └ C_KWIN: compiled by the EMWIN server
    │ │     │   └ WMO issue time DDhhmm
    │ │     └ originating office (CCCC)
    │ └ WMO heading TTAAii
    └ A_ or Z_ (Z_ is mostly graphics, but not only: the satellite
      ephemeris is Z_...TXT), so text vs graphic is the extension

International WMO products (Fiji, Australia, Argentina ...) carry no AWIPS
line inside the text, which NWS parsers require on line 2; the id in the
filename stands in for it.

rtl moves files older than a few hours from EMWIN/ into
EMWIN/archive/<UTC date of mtime>/ so the live directory stays small; see
archived_path().
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath

FILENAME = re.compile(
    r"^(?P<kind>[AZ])_(?P<wmo>[A-Z]{4}\d{2})(?P<cccc>[A-Z0-9]{4})(?P<ddhhmm>\d{6})[A-Z]{0,3}"
    r"_C_KWIN_(?P<rx>\d{14})_(?P<seq>\d+)-(?P<pri>\d)-(?P<awips>[A-Z0-9]+)\.(?P<ext>[A-Za-z0-9]+)$"
)

AWIPS_LINE = re.compile(r"^[A-Z0-9]{4,6}\s*$")


@dataclass(frozen=True)
class EmwinName:
    name: str
    is_text: bool
    wmo: str
    cccc: str
    ddhhmm: str
    received: datetime
    seq: int
    priority: int
    awips_long: str  # as in the filename, region suffix included
    ext: str

    @property
    def pil(self) -> str:
        """AWIPS product id without the 2-char region suffix (AFDTBWFL -> AFDTBW)."""
        a = self.awips_long
        return a[:-2] if len(a) > 5 else a

    @property
    def nnn(self) -> str:
        return self.pil[:3]


def parse_name(name: str) -> EmwinName | None:
    m = FILENAME.match(name)
    if not m:
        return None
    return EmwinName(
        name=name,
        is_text=m["ext"].lower() == "txt",
        wmo=m["wmo"],
        cccc=m["cccc"],
        ddhhmm=m["ddhhmm"],
        received=datetime.strptime(m["rx"], "%Y%m%d%H%M%S").replace(tzinfo=UTC),
        seq=int(m["seq"]),
        priority=int(m["pri"]),
        awips_long=m["awips"],
        ext=m["ext"].lower(),
    )


def normalize(raw: bytes, name: EmwinName) -> str:
    """Bytes off the share -> text a pyiem parser accepts.

    EMWIN text uses CR CR LF line ends and is not reliably UTF-8; latin-1
    never fails and maps the ASCII the products actually use unchanged.
    """
    text = raw.decode("latin-1").replace("\r\r\n", "\n").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\x01", "").replace("\x03", "").lstrip("\n")
    lines = text.split("\n")
    if len(lines) < 2 or not AWIPS_LINE.match(lines[1]):
        lines.insert(1, name.pil)
    return "\n".join(lines)


def archived_path(name: str, mtime: float) -> PurePosixPath:
    """Where rtl's archiver moves a file: EMWIN/archive/<UTC date of mtime>/name."""
    day = datetime.fromtimestamp(mtime, UTC).strftime("%Y-%m-%d")
    return PurePosixPath("EMWIN", "archive", day, name)
