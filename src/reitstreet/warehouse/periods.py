"""Period arithmetic: classify durations, fiscal year/quarter, knowledge-time versions."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from typing import Any


def classify_duration(days: int | None) -> tuple[str, int] | None:
    """(period_type, quarters covered) for a duration in days (inclusive count). None = unusable."""
    if days is None:
        return ("INSTANT", 0)
    if 80 <= days <= 100:
        return ("Q", 1)
    if 170 <= days <= 195:
        return ("YTD", 2)
    if 260 <= days <= 285:
        return ("YTD", 3)
    if 350 <= days <= 380:
        return ("FY", 4)
    return None


def _fye_date(year: int, fye: str) -> date:
    m, d = int(fye[:2]), int(fye[2:])
    while True:
        try:
            return date(year, m, d)
        except ValueError:
            d -= 1


def fiscal_period(period_end: date, fye: str | None) -> tuple[int, int]:
    """(fiscal_year, quarter 1..4). The fiscal year is named for the calendar year it
    ends in. Tolerates 52/53-week calendars (period ends up to 7 days after FYE)."""
    fye = fye if fye and len(fye) == 4 and fye.isdigit() else "1231"
    if period_end <= _fye_date(period_end.year - 1, fye) + timedelta(days=7):
        fy = period_end.year - 1  # e.g. a 52/53-week year ending Jan 3
    elif period_end <= _fye_date(period_end.year, fye) + timedelta(days=7):
        fy = period_end.year
    else:
        fy = period_end.year + 1
    prev_end = _fye_date(fy - 1, fye)
    q = round((period_end - prev_end).days / 91.31)
    return fy, max(1, min(4, q))


def build_versions(observations: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn observations {value, as_of, ...lineage} into knowledge-time versions.

    Sorted by as_of (publication date). A new version starts only when the value
    changes (a restatement); a repeat of the same value keeps the first publication.
    Two different values on the same day: the later observation wins. Each version gets
    version (1..n), known_from and known_to (the next version's known_from, or None).
    """
    out: list[dict[str, Any]] = []
    for o in sorted(observations, key=lambda x: (x["as_of"], x.get("tiebreak") or "")):
        if out and out[-1]["value"] == o["value"]:
            continue
        if out and out[-1]["as_of"] == o["as_of"]:
            out[-1] = {**o}
            if len(out) > 1 and out[-2]["value"] == o["value"]:
                out.pop()
            continue
        out.append({**o})
    for i, v in enumerate(out):
        v["version"] = i + 1
        v["known_from"] = v["as_of"]
        v["known_to"] = out[i + 1]["as_of"] if i + 1 < len(out) else None
    return out


def value_at(versions: list[dict[str, Any]], t: date) -> dict[str, Any] | None:
    cur = None
    for v in versions:
        if v["known_from"] <= t:
            cur = v
    return cur
