"""When is each company's next quarterly supplemental due, and which are late?

For a company that publishes supplementals (ref_ir_profile.expects_supplemental):
  latest   = latest supplemental period we hold, from any source (IR site, SEC 8-K, archive)
  next     = the quarter after it
  lag      = median days from period end to publication over its recent history, clamped to
             30..75; 45 when there are fewer than two observations. Fiscal Q4 gets +15 days
             (year-end audits push those supplementals out).
  due      = next + lag + grace (default 7 days)
Overdue when today is past due, or when an earnings release for `next` (or later) has been
out for 3+ days with no supplemental for it: most REITs publish both the same day.
A company with no supplemental at all has no baseline and is reported separately.
"""

from __future__ import annotations

import calendar
import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from ...core.db import Db
from .classify import period_from_date

DEFAULT_LAG = 45
MIN_LAG, MAX_LAG = 30, 75
Q4_EXTRA = 15
RELEASE_GRACE = 3
LIVE_LAG_MAX = 120  # publication lags above this are backfill, not real lags


@dataclass
class Expectation:
    company_id: int
    ticker: str
    status: str  # ok | overdue | no_baseline
    latest_period: date | None = None
    next_period: date | None = None
    due_date: date | None = None
    lag_days: int | None = None
    reason: str = ""


def next_quarter_end(d: date) -> date:
    m, y = d.month + 3, d.year
    if m > 12:
        m, y = m - 12, y + 1
    return date(y, m, calendar.monthrange(y, m)[1])


def typical_lag(lags: list[int]) -> int:
    live = [x for x in lags if 0 <= x <= LIVE_LAG_MAX]
    if len(live) < 2:
        return DEFAULT_LAG
    return int(min(MAX_LAG, max(MIN_LAG, statistics.median(live))))


def expect_company(
    company: dict[str, Any],
    supp: list[dict[str, Any]],
    releases: list[dict[str, Any]],
    today: date,
    grace_days: int = 7,
) -> Expectation:
    e = Expectation(company["company_id"], company["ticker"], "ok")
    periods = [r["period_end_date"] for r in supp if r["period_end_date"]]
    if not periods:
        e.status, e.reason = "no_baseline", "no supplemental with a known period yet"
        return e
    e.latest_period = max(periods)
    lags = [
        (r["published_date"] - r["period_end_date"]).days for r in supp if r["published_date"] and r["period_end_date"]
    ]
    e.lag_days = typical_lag(lags)
    e.next_period = next_quarter_end(e.latest_period)
    p = period_from_date(e.next_period, company.get("fiscal_year_end") or "1231")
    extra = Q4_EXTRA if p and p.fiscal_quarter == 4 else 0
    e.due_date = e.next_period + timedelta(days=e.lag_days + extra + grace_days)
    rel = [
        r for r in releases
        if r["period_end_date"] and r["period_end_date"] >= e.next_period and r["published_date"]
        and r["published_date"] + timedelta(days=RELEASE_GRACE) < today
    ]  # fmt: skip
    if rel:
        r0 = min(rel, key=lambda r: r["published_date"])
        e.status = "overdue"
        e.reason = (f"earnings release for period ending {r0['period_end_date']} published {r0['published_date']}; "
                    f"latest supplemental held is for {e.latest_period}")  # fmt: skip
    elif today > e.due_date:
        e.status = "overdue"
        e.reason = (f"supplemental for period ending {e.next_period} expected by {e.due_date} "
                    f"(typical lag {e.lag_days} days + grace); latest held is {e.latest_period}")  # fmt: skip
    return e


def expected_supplementals(db: Db, today: date | None = None, grace_days: int = 7) -> list[Expectation]:
    today = today or date.today()
    comps = db.query(
        """SELECT c.company_id, c.ticker, c.fiscal_year_end FROM ref_company c
           LEFT JOIN ref_ir_profile p ON p.company_id = c.company_id
           WHERE c.in_universe=1 AND COALESCE(p.expects_supplemental, 1)=1 ORDER BY c.ticker"""
    )
    docs = db.query(
        """SELECT company_id, doc_kind, period_end_date, published_date FROM stg_document
           WHERE doc_kind IN ('supplemental','earnings_release') AND company_id IS NOT NULL
             AND status IN ('fetched','parsed','needs_review','duplicate')"""
    )
    by: dict[int, dict[str, list[dict[str, Any]]]] = {}
    for d in docs:
        by.setdefault(d["company_id"], {"supplemental": [], "earnings_release": []})[d["doc_kind"]].append(d)
    out = []
    for c in comps:
        g = by.get(c["company_id"], {"supplemental": [], "earnings_release": []})
        out.append(expect_company(c, g["supplemental"], g["earnings_release"], today, grace_days))
    return out
