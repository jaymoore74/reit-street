"""What a table is (its kind) and how to read it (its orientation).

Kinds and their patterns live in reference/data/table_kinds.csv, so an analyst can add
a pattern without touching code. Three orientations:

  series    row label = metric, column = period        (FFO, income statement, leasing)
  segments  row label = segment, column header = metric (operating portfolio by market)
  records   one row = one thing with typed fields       (property, tenant, debt maturity
            year, lease expiration year, development project, loan)

The orientation is checked against the table itself: a "series" kind whose columns do
not read as periods is read as segments; any table whose row labels are mostly years
is a maturity or expiration ladder.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from importlib import resources

from .layout import Table
from .periods import column_role, parse_period

RECORD_KINDS = ("debt_maturity", "lease_expiration", "tenant", "development", "property", "debt_detail")
_FOOTNOTE = re.compile(r"\s*(\(\s*[a-z0-9]{1,2}\s*\)|\*+|†|‡)+\s*$", re.I)
_YEARISH = re.compile(
    r"^((19|20)\d\d|thereafter|after (19|20)\d\d|(19|20)\d\d\s*(and|&|-)\s*(thereafter|beyond|after)|beyond|"
    r"remainder of (19|20)\d\d|month[- ]to[- ]month|mtm|vacant|total)$",
    re.I,
)


@dataclass(frozen=True)
class KindRule:
    kind: str
    orientation: str
    title_rx: re.Pattern[str] | None
    body_rx: re.Pattern[str] | None
    weight: float


@dataclass
class Classification:
    kind: str
    orientation: str
    score: float  # 0..1
    period_cols: int
    value_cols: int


@lru_cache(maxsize=1)
def kind_rules() -> tuple[KindRule, ...]:
    text = resources.files("reitstreet.reference").joinpath("data/table_kinds.csv").read_text(encoding="utf-8")
    out = []
    for r in csv.DictReader(text.splitlines()):
        out.append(
            KindRule(
                r["kind"],
                r["orientation"],
                re.compile(r["title_regex"], re.I) if r["title_regex"] else None,
                re.compile(r["body_regex"], re.I) if r["body_regex"] else None,
                float(r["weight"] or 1),
            )
        )
    return tuple(out)


def clean_label(s: str) -> str:
    """Row or column label without footnote markers, page refs and trailing colons."""
    s = re.sub(r"\(\s*p(age)?\.?\s*\d+\s*\)", "", s, flags=re.I)
    prev = None
    while prev != s:
        prev = s
        s = _FOOTNOTE.sub("", s.strip())
    return re.sub(r"\s+", " ", s).strip().rstrip(":").strip()


def is_yearish(label: str) -> bool:
    return bool(_YEARISH.match(clean_label(label)))


def classify(t: Table, doc_period_end: date | None, fye_month: int = 12) -> Classification:
    title = " ".join([*(t.title_lines or []), t.title or ""])
    heading = t.page_heading or ""
    body = " ".join(r.label for r in t.rows) + " " + " ".join(c.header for c in t.columns)
    best: tuple[float, KindRule | None] = (0.0, None)
    for kr in kind_rules():
        s = 0.0
        if kr.title_rx is not None:
            if kr.title_rx.search(title):
                s += 0.7
            elif kr.title_rx.search(heading):
                s += 0.4
        if kr.body_rx is not None and kr.body_rx.search(body):
            s += 0.25
        s *= kr.weight
        if s > best[0]:
            best = (s, kr)

    value_cols = len(t.columns)
    period_cols = sum(
        1
        for c in t.columns
        if parse_period(c.header, doc_period_end, fye_month)[0] is not None or column_role(c.header) != "value"
    )
    labels = [clean_label(r.label) for r in t.rows if r.label]
    yearish = sum(1 for lab in labels if is_yearish(lab))
    year_cols = sum(1 for c in t.columns if is_yearish(c.header))

    score, rule = best
    kind = rule.kind if rule else "other"
    orientation = rule.orientation if rule else "series"

    # ladders: years down the side (or across the top for wide maturity tables)
    if labels and yearish / len(labels) >= 0.6 and kind not in ("debt_maturity", "lease_expiration"):
        lease_words = re.search(r"lease|expir|\bsf\b|square|abr|rent", title + " " + body, re.I)
        kind, orientation = ("lease_expiration" if lease_words else "debt_maturity"), "records"
        score = max(score, 0.5)
    if kind == "debt_maturity" and year_cols >= 3:
        orientation = "records"  # wide ladder, handled by the record reader

    if orientation == "series" and value_cols and period_cols / value_cols < 0.5:
        orientation = "segments"  # columns are not periods: metrics across, segments down
    if orientation == "segments" and value_cols and period_cols / value_cols >= 0.75 and kind not in RECORD_KINDS:
        orientation = "series"
    return Classification(kind, orientation, round(min(score, 1.0), 3), period_cols, value_cols)
