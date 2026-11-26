"""Classify a document link and infer the fiscal period it covers.

Everything here is pure (no I/O) so it is fully unit-tested. Rules are ordered: the
first matching rule wins. "Earnings Release and Supplemental" is a supplemental,
because the combined PDF carries the supplemental tables.
"""

from __future__ import annotations

import calendar
import re
from datetime import date
from urllib.parse import unquote, urlparse

from .models import Period

DOC_EXTENSIONS = (".pdf", ".xlsx", ".xls", ".xlsm")
# Paths that serve documents without a file extension, per IR platform.
DOC_PATH_HINTS = ("/static-files/", "/download/", "/~/media/files/", "/files/doc_", "/_assets/", "/db/")

EXCLUDE = re.compile(
    r"\b(proxy|sustainability|esg|corporate responsibility|impact report|annual meeting|governance|"
    r"code of (business )?conduct|charter|guidelines|tax (information|form)|form 8937|8937|dividend history|"
    r"fact ?sheet|transcript|webcast|audio)\b",
    re.I,
)
RULES: list[tuple[str, str, re.Pattern[str]]] = [
    (
        "supplemental",
        "supp_keyword",
        re.compile(
            r"supplement|\bsupp\b|_supp_|-supp-|financial (and operating |reporting )?package|"
            r"operating (and|&) financial data|earnings package|financial package|supplemental disclosure|"
            r"(quarterly|supplemental) information package|investor package|investor pkg",
            re.I,
        ),
    ),
    (
        "earnings_release",
        "release_keyword",
        re.compile(
            r"earnings release|press release|results release|news release|"
            r"reports? (first|second|third|fourth|full[- ]year)|quarterly results|ex[-_ ]?99\.?1\b",
            re.I,
        ),
    ),
    ("presentation", "presentation_keyword", re.compile(r"presentation|investor deck|slides|investor update", re.I)),
]


def is_document_url(url: str) -> bool:
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return False
    path = unquote(p.path).lower()
    return path.endswith(DOC_EXTENSIONS) or any(h in path for h in DOC_PATH_HINTS)


def _first_rule(hay: str) -> tuple[str, str] | None:
    if EXCLUDE.search(hay):
        return "exhibit_other", "exclude"
    for kind, rule, rx in RULES:
        if rx.search(hay):
            return kind, rule
    return None


def classify_link(text: str, url: str, context: str = "") -> tuple[str, str]:
    """(doc_kind, rule) from link text, then URL file name, then nearby text.

    One exception to text-first: when the text only says presentation or release (Q4 sites
    label the supplemental link "Presentation" or "Earnings Presentation") and the file name
    says supplemental, the file name wins."""
    name = unquote(urlparse(url).path.rsplit("/", 1)[-1]).replace("+", " ")
    for label, hay in (("text", text), ("url", name), ("context", context)):
        if not hay:
            continue
        hit = _first_rule(hay)
        if hit is None:
            continue
        kind, rule = hit
        if label == "text" and kind in ("presentation", "earnings_release"):
            by_name = _first_rule(name) if name else None
            if by_name and by_name[0] == "supplemental":
                return "supplemental", "supp_keyword_url_over_generic_text"
        return kind, f"{rule}_{label}"
    return "unknown", "no_rule"


# ------------------------------------------------------------------ periods
ORD = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4}
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m} | {
    m.lower(): i for i, m in enumerate(calendar.month_abbr) if m
}

_Y = r"(20\d{2}|19\d{2}|'?\d{2})"
QY_PATTERNS = [
    re.compile(rf"(?<![a-z0-9])q([1-4])[\s_\-.']*(?:fy)?{_Y}(?!\d)", re.I),  # Q3 2026, Q3'26, q3-26, Q3_2026
    re.compile(rf"(?<![a-z0-9])([1-4])q[\s_\-.']*(?:fy)?{_Y}(?!\d)", re.I),  # 3Q26, 3Q 2026
    re.compile(rf"(?<!\d){_Y}[\s_\-.]*q([1-4])(?![0-9])", re.I),  # 2026 Q3, 2026-Q3, 2026q3
    re.compile(
        rf"\b(first|second|third|fourth|1st|2nd|3rd|4th)[\s\-]+quarter(?:\s+(?:of\s+)?(?:fiscal\s+)?)?[\s,]*{_Y}\b",
        re.I,
    ),
    re.compile(r"\b(20\d{2})[\s,]+(first|second|third|fourth|1st|2nd|3rd|4th)[\s\-]+quarter\b", re.I),
]
FY_PATTERN = re.compile(r"\b(?:full[- ]year|fiscal(?: year)?|annual|year[- ]end(?:ed)?|fy)[\s\-]*(20\d{2})\b", re.I)
DATE_PATTERNS = [
    re.compile(
        r"(?<!\d)(0?[1-9]|1[0-2])[.\-/_](0?[1-9]|[12]\d|3[01])[.\-/_](20\d{2}|\d{2})(?!\d)"
    ),  # 09.30.2026, 9-30-26
    re.compile(r"(?<!\d)(20\d{2})[.\-/_](0?[1-9]|1[0-2])[.\-/_](0?[1-9]|[12]\d|3[01])(?!\d)"),  # 2026-09-30
    re.compile(
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december|"
        r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\.?\s+(\d{1,2}),?\s+(20\d{2})\b",
        re.I,
    ),
]
URL_QUARTER = re.compile(r"/(20\d{2})/q([1-4])/", re.I)


def _year(tok: str) -> int:
    tok = tok.lstrip("'")
    y = int(tok)
    return y + 2000 if y < 100 else y


def _month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def quarter_end(fiscal_year: int, quarter: int, fye_mmdd: str | None = "1231") -> date:
    """Period end of fiscal quarter `quarter` of fiscal year `fiscal_year`, where the fiscal
    year ends in calendar year `fiscal_year` on month MM of fye_mmdd (SEC convention)."""
    fye_month = int((fye_mmdd or "1231")[:2]) or 12
    months_back = 3 * (4 - quarter)
    m = fye_month - months_back
    y = fiscal_year
    while m <= 0:
        m += 12
        y -= 1
    return _month_end(y, m)


def period_from_date(d: date, fye_mmdd: str | None = "1231") -> Period | None:
    """Map a quarter-end date to (fiscal year, quarter). None if not a quarter end."""
    fye_month = int((fye_mmdd or "1231")[:2]) or 12
    if d != _month_end(d.year, d.month) and d.day < 28:
        return None
    diff = (fye_month - d.month) % 12
    if diff % 3:
        return None
    q = 4 - diff // 3
    fy = d.year if d.month <= fye_month else d.year + 1
    return Period(fy, q, _month_end(d.year, d.month), "")


def _plausible(y: int, today: date | None) -> bool:
    t = today or date.today()
    return 1995 <= y <= t.year + 1


def infer_period(text: str, fye_mmdd: str | None = "1231", *, today: date | None = None) -> Period | None:
    """Fiscal period from free text (link text, file name, heading). Quarter tokens win over
    dates because a date in a file name is often the publish date, not the period end."""
    if not text:
        return None
    t = unquote(text).replace("+", " ")
    for i, rx in enumerate(QY_PATTERNS):
        m = rx.search(t)
        if not m:
            continue
        if i == 2:
            y, q = _year(m.group(1)), int(m.group(2))
        elif i == 3:
            q, y = ORD[m.group(1).lower()], _year(m.group(2))
        elif i == 4:
            y, q = int(m.group(1)), ORD[m.group(2).lower()]
        else:
            q, y = int(m.group(1)), _year(m.group(2))
        if _plausible(y, today):
            return Period(y, q, quarter_end(y, q, fye_mmdd), "")
    m = URL_QUARTER.search(t)
    if m and _plausible(int(m.group(1)), today):
        y, q = int(m.group(1)), int(m.group(2))
        return Period(y, q, quarter_end(y, q, fye_mmdd), "")
    for i, rx in enumerate(DATE_PATTERNS):
        m = rx.search(t)
        if not m:
            continue
        try:
            if i == 0:
                d = date(_year(m.group(3)), int(m.group(1)), int(m.group(2)))
            elif i == 1:
                d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            else:
                d = date(int(m.group(3)), MONTHS[m.group(1).lower().rstrip(".")[:3]], int(m.group(2)))
        except (ValueError, KeyError):
            continue
        p = period_from_date(d, fye_mmdd)
        if p and _plausible(p.period_end.year, today):
            return p
    m = FY_PATTERN.search(t)
    if m and _plausible(int(m.group(1)), today):
        y = int(m.group(1))
        return Period(y, 4, quarter_end(y, 4, fye_mmdd), "")
    return None


def link_period(text: str, url: str, context: str, fye_mmdd: str | None, *, today: date | None = None) -> Period | None:
    """Try link text, then the URL, then nearby text; record which one answered."""
    for source, hay in (("anchor", text), ("url", unquote(urlparse(url).path)), ("context", context)):
        p = infer_period(hay, fye_mmdd, today=today)
        if p:
            p.source = source
            return p
    return None
