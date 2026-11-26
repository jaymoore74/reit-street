"""Column headers to reporting periods.

Handles the header styles seen across REIT supplementals:
  "Three Months Ended September 30, 2026"     -> quarter ending 2026-09-30
  "For the Three Months Ended Sep 30, 2026"   -> same; the year may sit on its own line
  "Nine Months Ended ..." / "Six Months" / "Year Ended December 31, 2025" / "FY 2025"
  "3Q26", "Q3 2026", "Q3'26", "3Q 2026", "Third Quarter 2026", "3Q26 QTD", "YTD 3Q26"
  "September 30, 2026", "9/30/2026", "As of Sep 30, 2026"  -> balance date (instant)
and the column roles that are not a period value:
  "Change", "% Change", "YoY Δ", "Percent Change", "% Growth", "Variance", "bps"  -> change
  "% of Total", "% of ABR", "Share"                                              -> share
Year order varies by company (some print 2025 before 2026), so every column is read on
its own; nothing assumes the current period comes first.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
MONTHS["sept"] = 9
_MON = (
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?"
    r"|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
)
ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "1st": 1, "2nd": 2, "3rd": 3, "4th": 4}
NUM_WORDS = {"three": 3, "six": 6, "nine": 9, "twelve": 12, "3": 3, "6": 6, "9": 9, "12": 12}

CHANGE_RX = re.compile(
    r"(%|percent(age)?)\s*(change|chg|growth|var)|\bchange\b|\bchg\b|\bgrowth\b|\bvariance\b|y\s*/?\s*o\s*/?\s*y|"
    r"yoy|\bbps\b|Δ|∆|\bvs\.?\b|increase\s*/\s*\(?decrease|inc\s*/\s*\(?dec",
    re.I,
)
SHARE_RX = re.compile(
    r"%\s*of\s*(total|abr|noi|portfolio|revenue|rent|gross|sf|nrsf|units)|\bshare of\b|percent of total", re.I
)


@dataclass(frozen=True)
class Period:
    start: date | None
    end: date
    kind: str  # Q, YTD, FY, TTM, INSTANT

    @property
    def months(self) -> int | None:
        if self.start is None:
            return None
        return (self.end.year - self.start.year) * 12 + self.end.month - self.start.month + 1


def month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def back(end: date, months: int) -> date:
    """First day of the period of `months` months that ends on `end` (month-end aligned)."""
    y, m = end.year, end.month - months + 1
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def _yy(s: str) -> int:
    y = int(s.lstrip("'"))
    return 2000 + y if y < 100 else y


def quarter_end(q: int, y: int, fye_month: int = 12) -> date:
    """End of fiscal quarter q of fiscal year y (named for the calendar year it ends in)."""
    m = (fye_month - 12 + 3 * q) % 12 or 12
    year = y if m <= fye_month else y - 1
    return month_end(year, m)


def column_role(header: str) -> str:
    if SHARE_RX.search(header):
        return "share"
    if CHANGE_RX.search(header):
        return "change"
    return "value"


def parse_period(header: str, doc_period_end: date | None = None, fye_month: int = 12) -> tuple[Period | None, float]:
    """(period, confidence) for a column header. Confidence is 0 when no period was found."""
    h = " ".join(header.replace("’", "'").split())
    if not h:
        return None, 0.0
    low = h.lower()
    year_m = re.findall(r"\b(19[5-9]\d|20\d\d)\b", h)

    # 1) "<n> months ended <date>" (the year may be the last token, or on its own header line)
    m = re.search(r"\b(three|six|nine|twelve|3|6|9|12)[\s-]+months?\s+(ended|ending)\b", low)
    if m:
        months = NUM_WORDS[m.group(1)]
        kind = "Q" if months == 3 else ("FY" if months == 12 else "YTD")
        rest, rc = parse_period(h[m.end() :], doc_period_end, fye_month)
        if rest is not None and rest.kind == "INSTANT":
            return Period(back(rest.end, months), rest.end, kind), min(0.97, rc + 0.05)
        mm = re.search(_MON, low[m.end() :])
        if mm:
            y = int(year_m[-1]) if year_m else (doc_period_end.year if doc_period_end else None)
            if y is not None:
                end = month_end(y, MONTHS[mm.group(1).rstrip(".")[:3]])
                return Period(back(end, months), end, kind), 0.95 if year_m else 0.7
        if doc_period_end is not None:
            return Period(back(doc_period_end, months), doc_period_end, kind), 0.6
        return None, 0.0

    # 2) "year(s) ended <Month> <day>, <year>", "FY 2025", "full year 2025", "fiscal 2025"
    m = re.search(r"\byears?\s+ended\s+" + _MON, low)
    if m and year_m:
        mon = MONTHS[m.group(1).rstrip(".")[:3]]
        end = month_end(int(year_m[-1]), mon)
        return Period(back(end, 12), end, "FY"), 0.95
    m = re.search(r"\b(fy|full[- ]year|fiscal(?: year)?)\s*'?(\d{2}|\d{4})\b", low)
    if m:
        end = month_end(_yy(m.group(2)), fye_month)
        return Period(back(end, 12), end, "FY"), 0.9

    # 3) quarter tokens: 3Q26, Q3 2026, Q3'26, 3Q 2026, third quarter 2026, 3Q26 QTD
    m = re.search(r"\b([1-4])\s*q\s*'?(\d{2}|\d{4})\b|\bq([1-4])\s*['\s]?\s*(\d{2}|\d{4})\b", low)
    if m:
        q = int(m.group(1) or m.group(3))
        y = _yy(m.group(2) or m.group(4))
        end = quarter_end(q, y, fye_month)
        if re.search(r"\bytd\b|year[- ]to[- ]date", low):
            return Period(back(end, 3 * q), end, "YTD" if q < 4 else "FY"), 0.9
        if re.search(r"\bttm\b|trailing", low):
            return Period(back(end, 12), end, "TTM"), 0.85
        return Period(back(end, 3), end, "Q"), 0.95
    m = re.search(r"\b(first|second|third|fourth|1st|2nd|3rd|4th)\s+quarter\s*(?:of\s*)?(\d{4})?", low)
    if m and (m.group(2) or year_m):
        q = ORD[m.group(1)]
        end = quarter_end(q, int(m.group(2) or year_m[-1]), fye_month)
        return Period(back(end, 3), end, "Q"), 0.9

    # 4) year to date: "YTD 2026", "2026 YTD", "year-to-date"
    if re.search(r"\bytd\b|year[- ]to[- ]date", low) and doc_period_end:
        y = int(year_m[-1]) if year_m else doc_period_end.year
        end = month_end(y, doc_period_end.month)
        return Period(back(end, end.month if fye_month == 12 else 3), end, "YTD"), 0.7

    # 5) TTM / trailing twelve months ended ...
    if re.search(r"\bttm\b|trailing\s+(twelve|12)", low) and doc_period_end:
        return Period(back(doc_period_end, 12), doc_period_end, "TTM"), 0.75

    # 6) dates: "September 30, 2026", "Sep. 30, 2026", "9/30/2026", "as of ..."
    m = re.search(_MON + r"\s*(\d{1,2}),?\s*(\d{4})", low)
    if m:
        mon = MONTHS[m.group(1).rstrip(".")[:3]]
        return Period(None, month_end(int(m.group(3)), mon), "INSTANT"), 0.95
    m = re.search(r"\b(\d{1,2})[- ]" + _MON + r"[- ]'?(\d{2}|\d{4})\b", low)  # "30-Sep-26"
    if m:
        try:
            d = date(_yy(m.group(3)), MONTHS[m.group(2).rstrip(".")[:3]], int(m.group(1)))
            return Period(None, d, "INSTANT"), 0.92
        except ValueError:
            pass
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", low)
    if m:
        try:
            d = date(_yy(m.group(3)), int(m.group(1)), int(m.group(2)))
            return Period(None, d, "INSTANT"), 0.9
        except ValueError:
            pass
    m = re.search(r"\b" + _MON + r"\s*('\d{2}|\d{4})\b", low)  # "Sep 2026", "Dec '25"
    if m:
        mon = MONTHS[m.group(1).rstrip(".")[:3]]
        return Period(None, month_end(_yy(m.group(2)), mon), "INSTANT"), 0.8

    # 7) a bare year: ambiguous without context
    if len(year_m) == 1 and re.fullmatch(r"(fy\s*)?\d{4}\s*(actual|a|e|estimate)?", low.strip()):
        end = month_end(int(year_m[0]), fye_month)
        return Period(back(end, 12), end, "FY"), 0.45
    return None, 0.0


def table_default_period(context: str, doc_period_end: date | None, fye_month: int = 12) -> tuple[Period | None, float]:
    """Period stated in a table title ("Debt Summary as of September 30, 2026",
    "Third Quarter 2026 Leasing Activity"), used for columns whose header has none."""
    p, c = parse_period(context, doc_period_end, fye_month)
    if p is not None:
        return p, min(c, 0.85)
    return None, 0.0
