"""Normalization of property names, addresses and locations for matching.

Pure functions, no I/O. The rules follow USPS Publication 28 abbreviations for street
suffixes, directionals and secondary unit designators, which is what geocoders and most
REIT filings use. Normalized forms are for matching only; the warehouse keeps the
text as reported.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

STATES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR", "CALIFORNIA": "CA", "COLORADO": "CO",
    "CONNECTICUT": "CT", "DELAWARE": "DE", "DISTRICT OF COLUMBIA": "DC", "FLORIDA": "FL", "GEORGIA": "GA",
    "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
    "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD", "MASSACHUSETTS": "MA",
    "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT",
    "NEBRASKA": "NE", "NEVADA": "NV", "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM",
    "NEW YORK": "NY", "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK",
    "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD",
    "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY", "PUERTO RICO": "PR", "GUAM": "GU",
    "VIRGIN ISLANDS": "VI", "WASHINGTON DC": "DC", "WASHINGTON D C": "DC",
}  # fmt: skip
STATE_CODES = frozenset(STATES.values())
# Canadian provinces appear in a few US REIT filings.
PROVINCES = {"ONTARIO": "ON", "QUEBEC": "QC", "BRITISH COLUMBIA": "BC", "ALBERTA": "AB", "MANITOBA": "MB",
             "SASKATCHEWAN": "SK", "NOVA SCOTIA": "NS", "NEW BRUNSWICK": "NB"}  # fmt: skip

SUFFIXES = {
    "ALLEY": "ALY", "AVENUE": "AVE", "AV": "AVE", "AVEN": "AVE", "BOULEVARD": "BLVD", "BOUL": "BLVD", "BLV": "BLVD",
    "CIRCLE": "CIR", "CIRC": "CIR", "COURT": "CT", "CRT": "CT", "COVE": "CV", "CROSSING": "XING", "CRSSNG": "XING",
    "DRIVE": "DR", "DRV": "DR", "EXPRESSWAY": "EXPY", "EXPRESS": "EXPY", "FREEWAY": "FWY", "HIGHWAY": "HWY",
    "HIWAY": "HWY", "LANE": "LN", "LOOP": "LOOP", "PARKWAY": "PKWY", "PKY": "PKWY", "PARKWY": "PKWY",
    "PIKE": "PIKE", "PLACE": "PL", "PLAZA": "PLZ", "POINT": "PT", "ROAD": "RD", "ROUTE": "RTE", "SQUARE": "SQ",
    "STREET": "ST", "STR": "ST", "TERRACE": "TER", "TRAIL": "TRL", "TURNPIKE": "TPKE", "WAY": "WAY",
    "CENTER": "CTR", "CENTRE": "CTR", "CROSSROADS": "XRDS", "COMMONS": "CMNS", "HEIGHTS": "HTS",
    "JUNCTION": "JCT", "MOUNT": "MT", "MOUNTAIN": "MTN", "PASS": "PASS", "RIDGE": "RDG", "RUN": "RUN",
    "STATION": "STA", "VALLEY": "VLY", "VIEW": "VW", "VILLAGE": "VLG",
}  # fmt: skip
DIRECTIONALS = {
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W", "NORTHEAST": "NE", "NORTHWEST": "NW",
    "SOUTHEAST": "SE", "SOUTHWEST": "SW",
}  # fmt: skip
ORDINALS = {
    "FIRST": "1ST", "SECOND": "2ND", "THIRD": "3RD", "FOURTH": "4TH", "FIFTH": "5TH", "SIXTH": "6TH",
    "SEVENTH": "7TH", "EIGHTH": "8TH", "NINTH": "9TH", "TENTH": "10TH",
}  # fmt: skip
UNIT_RX = re.compile(
    r"\s*(?:,\s*)?(?:#\s*\w+|\b(?:SUITE|STE|UNIT|APT|APARTMENT|BLDG|BUILDING|FLOOR|FL|RM|ROOM|SPC|SPACE|LOT)\b\.?\s*[\w-]+)",
)
NAME_STOP = frozenset({"THE", "AT", "OF", "AND", "A"})
ZIP_RX = re.compile(r"^(\d{5})(?:-?\d{4})?$")


def ascii_upper(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return s.upper()


def _clean(s: str) -> str:
    s = ascii_upper(s).replace("&", " AND ")
    s = re.sub(r"[^A-Z0-9#\- ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def state_code(s: str | None) -> str | None:
    """'Texas', 'tx', 'Tex.' -> 'TX'. Unknown input is returned upper-cased and trimmed."""
    if not s:
        return None
    t = re.sub(r"[^A-Z ]", " ", ascii_upper(s))
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return None
    if t in STATE_CODES:
        return t
    if t in STATES:
        return STATES[t]
    if t in PROVINCES:
        return PROVINCES[t]
    if t.replace(" ", "") in STATE_CODES:
        return t.replace(" ", "")
    return t[:64]


def zip5(s: str | None) -> str | None:
    if not s:
        return None
    m = ZIP_RX.match(s.strip())
    return m.group(1) if m else None


def normalize_street(s: str | None) -> str | None:
    """'1200 North Main Street, Suite 300' -> '1200 N MAIN ST'.

    Secondary units are dropped (a building, not a suite, is the property). A range
    '100-110 Main St' keeps its first number so the same building matches either way."""
    if not s or not s.strip():
        return None
    t = ascii_upper(s)
    t = UNIT_RX.sub("", t)
    t = _clean(t).replace("#", "")
    words = t.split()
    if not words:
        return None
    # house number range: 100-110 -> 100 ; 100 - 110 -> 100
    m = re.match(r"^(\d+)[A-Z]?-\d+[A-Z]?$", words[0])
    if m:
        words[0] = m.group(1)
    elif len(words) > 2 and words[0].isdigit() and words[1] == "-" and words[2].isdigit():
        words = [words[0], *words[3:]]
    out = []
    for i, w in enumerate(words):
        last = i == len(words) - 1
        if w in DIRECTIONALS and (i <= 1 or last):
            out.append(DIRECTIONALS[w])
        elif w in ORDINALS:
            out.append(ORDINALS[w])
        elif w in SUFFIXES and i > 0:
            out.append(SUFFIXES[w])
        else:
            out.append(w)
    return " ".join(x for x in out if x != "-") or None


def normalize_city(s: str | None) -> str | None:
    if not s or not s.strip():
        return None
    t = _clean(s).replace("#", "")
    t = re.sub(r"^(ST|SAINT)\b", "ST", t)
    t = re.sub(r"^(FT|FORT)\b", "FT", t)
    t = re.sub(r"^(MT|MOUNT)\b", "MT", t)
    return t or None


def normalize_name(s: str | None) -> str | None:
    """Property names: case, accents, punctuation and filler words removed."""
    if not s or not s.strip():
        return None
    t = _clean(s).replace("#", " ").replace("-", " ")
    words = [w for w in t.split() if w not in NAME_STOP]
    return " ".join(words) or None


def name_tokens(s: str | None) -> frozenset[str]:
    n = normalize_name(s)
    return frozenset(n.split()) if n else frozenset()


def _h(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def address_key(street: str | None, city: str | None, state: str | None, postal: str | None) -> str | None:
    """Identity of a street address: normalized street plus ZIP5, or plus city and state.

    None without a numbered street: a city alone does not identify a building."""
    st = normalize_street(street)
    if not st or not re.search(r"\d", st):
        return None
    z = zip5(postal)
    if z:
        return _h("z", st, z)
    c, s = normalize_city(city), state_code(state)
    if c and s:
        return _h("c", st, c, s)
    return None


@dataclass(frozen=True)
class Location:
    city: str | None
    state: str | None
    country: str | None = None


LOC_RX = re.compile(r"^\s*(?P<city>[^,]+?)\s*,\s*(?P<state>[A-Za-z .]{2,30})\s*(?:\d{5}(?:-\d{4})?)?\s*$")


def parse_location(s: str | None) -> Location:
    """'Dallas, TX' / 'Dallas, Texas' -> Location('Dallas', 'TX'). A bare state ('Texas') -> state only."""
    if not s or not s.strip():
        return Location(None, None)
    m = LOC_RX.match(s)
    if m:
        st = state_code(m.group("state"))
        if st in STATE_CODES:
            return Location(
                m.group("city").strip().title() if m.group("city").isupper() else m.group("city").strip(), st, "US"
            )
        if st in PROVINCES.values():
            return Location(m.group("city").strip(), st, "CA")
    st = state_code(s)
    if st in STATE_CODES:
        return Location(None, st, "US")
    return Location(None, None)


YEAR_RX = re.compile(r"\b(18[5-9]\d|19\d\d|20\d\d)\b")


def first_year(s: str | int | None) -> int | None:
    """'1998/2004' -> 1998; 'Various' -> None."""
    if s is None:
        return None
    if isinstance(s, int):
        return s if 1850 <= s <= 2100 else None
    m = YEAR_RX.search(str(s))
    return int(m.group(1)) if m else None


def last_year(s: str | None) -> int | None:
    if not s:
        return None
    ys = YEAR_RX.findall(str(s))
    return int(ys[-1]) if ys else None


AGGREGATE_RX = re.compile(
    r"^\s*corporate(?:\s+and\s+other)?\s*$|\b(various|other properties|all other|miscellaneous|portfolio of|"
    r"\d+\s+(?:properties|buildings|communities|hotels|facilities|centers|assets|stores)|"
    r"total|subtotal|land held|construction in progress|development pipeline|unallocated|intangible)\b",
    re.IGNORECASE,
)
COUNT_RX = re.compile(
    r"\b(\d{1,5})\s+(?:properties|buildings|communities|hotels|facilities|centers|assets|stores)\b", re.I
)


def aggregate_signal(name: str | None) -> tuple[bool, int | None]:
    """Whether a Schedule III style row stands for several properties, and how many if stated."""
    if not name:
        return False, None
    if AGGREGATE_RX.search(name):
        m = COUNT_RX.search(name)
        return True, int(m.group(1)) if m else None
    return False, None
