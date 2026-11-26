"""Parse XML from outside sources without entity tricks.

ElementTree never fetches external entities, and expat 2.4.1+ caps entity expansion.
XBRL instances, label linkbases and XLSX parts never need a DTD, so any document type
declaration is refused outright: that closes billion-laughs and quadratic blowup."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .errors import ParseError

_DTD = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


def fromstring(data: bytes | str) -> ET.Element:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if _DTD.search(raw):
        raise ParseError("XML with a DTD or entity declaration is refused")
    try:
        return ET.fromstring(raw)  # noqa: S314 - DTDs refused above; ElementTree resolves no external entities
    except ET.ParseError as e:
        raise ParseError(f"not well-formed XML: {e}") from e
