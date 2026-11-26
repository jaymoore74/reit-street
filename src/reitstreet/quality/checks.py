"""Data quality checks. Run with `reitstreet dq` (after every sync in the scheduled
scripts). Results land in ops_dq_result; failures of severity 'error' raise an alert.

Families:
  contract.*   structural guarantees the warehouse promises (lineage present, one current
               version, unbroken knowledge chains, raw objects present and intact)
  accounting.* identities that hold in real financial statements
  recon.*      the same number from two sources must agree (supplemental vs XBRL)
  anomaly.*    values that are possible but suspicious, for a human to look at
  freshness.*  sources that have gone quiet
  ops.*        backlog in the dead-letter queue

Add a check: write a function (db, ctx) -> list[Finding], decorate with @check(...).
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ..core.db import Db
from ..core.rawstore import IntegrityError, RawStore


@dataclass
class Finding:
    subject: str
    observed: str | None = None
    expected: str | None = None
    company_id: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class Ctx:
    store: RawStore | None
    sync_max_age_days: int = 3
    filing_max_age_days: int = 140
    sample_objects: int = 100
    supplemental_grace_days: int = 7
    ir_failures_threshold: int = 3


@dataclass(frozen=True)
class Check:
    code: str
    severity: str
    description: str
    fn: Callable[[Db, Ctx], list[Finding]]


CHECKS: list[Check] = []


def check(
    code: str, severity: str, description: str
) -> Callable[[Callable[[Db, Ctx], list[Finding]]], Callable[[Db, Ctx], list[Finding]]]:
    def deco(fn: Callable[[Db, Ctx], list[Finding]]) -> Callable[[Db, Ctx], list[Finding]]:
        CHECKS.append(Check(code, severity, description, fn))
        return fn

    return deco


def _metric_rows(db: Db, sql: str, **p: Any) -> list[dict[str, Any]]:
    return db.query(sql, p)


# ------------------------------------------------------------------ contract
@check("contract.lineage", "error", "Every live warehouse value points back to its source")
def lineage(db: Db, ctx: Ctx) -> list[Finding]:
    rows = db.query(
        """SELECT value_id, company_id, source_type FROM wh_metric_value WHERE retired_at IS NULL AND (
             (source_type='xbrl' AND (source_fact_id IS NULL OR source_sha256 IS NULL OR source_accession IS NULL))
          OR (source_type='derived' AND (derivation IS NULL OR source_accession IS NULL))
          OR (source_type='supplemental'
              AND (source_doc_id IS NULL OR source_item_id IS NULL OR source_sha256 IS NULL)))
           LIMIT 500"""
    )
    return [
        Finding(f"value_id {r['value_id']}", r["source_type"], "lineage columns set", r["company_id"]) for r in rows
    ]


@check("contract.single_current", "error", "At most one current (known_to NULL) version per value key and source")
def single_current(db: Db, ctx: Ctx) -> list[Finding]:
    rows = db.query(
        """SELECT company_id, metric_id, period_type, period_end, segment, source_type, COUNT(*) n
           FROM wh_metric_value WHERE retired_at IS NULL AND known_to IS NULL
           GROUP BY company_id, metric_id, period_type, period_start_key, period_end, segment, source_type
           HAVING n > 1 LIMIT 500"""
    )
    return [
        Finding(
            f"metric {r['metric_id']} {r['period_type']} {r['period_end']} {r['source_type']}",
            str(r["n"]),
            "1",
            r["company_id"],
        )
        for r in rows
    ]


@check("contract.knowledge_chain", "error", "Versions are contiguous: each known_to equals the next known_from")
def knowledge_chain(db: Db, ctx: Ctx) -> list[Finding]:
    rows = db.query(
        """SELECT * FROM (
             SELECT company_id, metric_id, period_end, source_type, version, known_to,
                    LEAD(known_from) OVER w AS next_from, LEAD(version) OVER w AS next_version
             FROM wh_metric_value WHERE retired_at IS NULL
             WINDOW w AS (PARTITION BY company_id, metric_id, period_type, period_start_key, period_end, segment,
                          source_type ORDER BY known_from)) x
           WHERE (next_from IS NULL AND known_to IS NOT NULL)
              OR (next_from IS NOT NULL AND (known_to IS NULL OR known_to <> next_from OR next_version <> version + 1))
           LIMIT 500"""
    )
    return [
        Finding(
            f"metric {r['metric_id']} {r['period_end']} {r['source_type']} v{r['version']}",
            f"known_to {r['known_to']}",
            f"next known_from {r['next_from']}",
            r["company_id"],
        )
        for r in rows
    ]


@check("contract.raw_objects", "error", "A sample of raw objects exists in the raw store and matches its hash")
def raw_objects(db: Db, ctx: Ctx) -> list[Finding]:
    if ctx.store is None:
        return []
    shas = [
        r["sha256"]
        for r in db.query(
            "SELECT sha256 FROM raw_object ORDER BY first_seen_at DESC LIMIT :n", {"n": ctx.sample_objects // 2}
        )
    ]
    total = int(db.scalar("SELECT COUNT(*) FROM raw_object") or 0)
    if total > len(shas):
        offsets = random.sample(range(total), min(ctx.sample_objects // 2, total))
        for off in offsets:
            s = db.scalar("SELECT sha256 FROM raw_object ORDER BY sha256 LIMIT 1 OFFSET :o", {"o": off})
            if s:
                shas.append(s)
    out = []
    for sha in set(shas):
        try:
            ctx.store.get(sha)
        except FileNotFoundError:
            out.append(Finding(f"raw_object {sha}", "missing", "present"))
        except (IntegrityError, OSError, EOFError) as e:
            out.append(Finding(f"raw_object {sha}", f"corrupt: {e}", "sha256 match"))
    return out


# ------------------------------------------------------------------ accounting
CURRENT_XBRL = """
  SELECT v.company_id, v.period_type, v.period_start, v.period_end, v.value
  FROM wh_metric_value v JOIN ref_metric m ON m.metric_id = v.metric_id
  WHERE m.metric_code = :code AND v.retired_at IS NULL AND v.known_to IS NULL AND v.segment = 'Total'
    AND v.source_type IN ('xbrl','derived')"""


@check("accounting.balance_sheet", "warn", "Total assets = total liabilities + total equity (within 2%)")
def balance_sheet(db: Db, ctx: Ctx) -> list[Finding]:
    rows = db.query(
        f"""SELECT a.company_id, a.period_end, a.value AS assets, l.value AS liabilities, e.value AS equity
            FROM ({CURRENT_XBRL.replace(":code", "'TOTAL_ASSETS'")}) a
            JOIN ({CURRENT_XBRL.replace(":code", "'TOTAL_LIABILITIES'")}) l
              ON l.company_id=a.company_id AND l.period_end=a.period_end
            JOIN ({CURRENT_XBRL.replace(":code", "'TOTAL_EQUITY'")}) e
              ON e.company_id=a.company_id AND e.period_end=a.period_end
            WHERE ABS(a.value - l.value - e.value) > 0.02 * ABS(a.value)"""
    )
    return [
        Finding(
            f"balance sheet {r['period_end']}",
            f"A={r['assets']} L+E={r['liabilities'] + r['equity']}",
            "within 2% (mezzanine equity can explain a gap)",
            r["company_id"],
        )
        for r in rows
    ]


@check("accounting.quarters_sum_to_fy", "warn", "Four quarters add up to the fiscal year (revenue, net income)")
def quarters_sum(db: Db, ctx: Ctx) -> list[Finding]:
    out = []
    for code in ("TOTAL_REVENUE", "NET_INCOME"):
        rows = db.query(
            f"""SELECT fy.company_id, fy.period_end, fy.value AS fy_value, SUM(q.value) AS q_sum, COUNT(*) AS nq
                FROM ({CURRENT_XBRL}) fy
                JOIN ({CURRENT_XBRL}) q ON q.company_id=fy.company_id AND q.period_type='Q'
                  AND q.period_end > fy.period_start AND q.period_end <= fy.period_end
                WHERE fy.period_type='FY'
                GROUP BY fy.company_id, fy.period_end, fy.value
                HAVING nq = 4 AND ABS(q_sum - fy_value) > GREATEST(0.005 * ABS(fy_value), 1000)""",
            {"code": code},
        )
        out += [
            Finding(f"{code} FY {r['period_end']}", str(r["q_sum"]), str(r["fy_value"]), r["company_id"]) for r in rows
        ]
    return out


@check("anomaly.sign", "warn", "Values that should never be negative")
def sign(db: Db, ctx: Ctx) -> list[Finding]:
    rows = db.query(
        """SELECT v.company_id, m.metric_code, v.period_end, v.value FROM wh_metric_value v
           JOIN ref_metric m ON m.metric_id=v.metric_id
           WHERE v.retired_at IS NULL AND v.known_to IS NULL AND v.value < 0
             AND m.metric_code IN ('TOTAL_ASSETS','TOTAL_REVENUE','REAL_ESTATE_GROSS','SHARES_OUTSTANDING',
                                   'OCCUPANCY','TOTAL_DEBT') LIMIT 500"""
    )
    return [Finding(f"{r['metric_code']} {r['period_end']}", str(r["value"]), ">= 0", r["company_id"]) for r in rows]


@check("anomaly.qoq_jump", "warn", "Quarter-over-quarter moves over 50% in revenue, NOI or FFO")
def qoq_jump(db: Db, ctx: Ctx) -> list[Finding]:
    rows = db.query(
        """SELECT * FROM (
             SELECT v.company_id, m.metric_code, v.source_type, v.period_end, v.value,
                    LAG(v.value) OVER (PARTITION BY v.company_id, v.metric_id, v.source_type, v.segment
                                       ORDER BY v.period_end) AS prev
             FROM wh_metric_value v JOIN ref_metric m ON m.metric_id=v.metric_id
             WHERE v.retired_at IS NULL AND v.known_to IS NULL AND v.period_type='Q' AND v.segment='Total'
               AND m.metric_code IN ('TOTAL_REVENUE','NOI','FFO_NAREIT','CORE_FFO')) x
           WHERE prev IS NOT NULL AND prev <> 0 AND ABS(value / prev - 1) > 0.5 LIMIT 500"""
    )
    return [
        Finding(
            f"{r['metric_code']} Q {r['period_end']} ({r['source_type']})",
            str(r["value"]),
            f"prior {r['prev']} +/-50%",
            r["company_id"],
        )
        for r in rows
    ]


# ------------------------------------------------------------------ recon
@check("recon.supplemental_vs_xbrl", "warn", "Supplemental values agree with XBRL for the same metric and period (1%)")
def supp_vs_xbrl(db: Db, ctx: Ctx) -> list[Finding]:
    rows = db.query(
        """SELECT s.company_id, m.metric_code, s.period_type, s.period_end, s.value AS supp, x.value AS xbrl,
                  s.source_doc_id, s.source_page_no, x.source_accession
           FROM wh_metric_value s
           JOIN wh_metric_value x ON x.company_id=s.company_id AND x.metric_id=s.metric_id
             AND x.period_type=s.period_type AND x.period_end=s.period_end AND x.segment=s.segment
             AND x.source_type IN ('xbrl','derived') AND x.retired_at IS NULL AND x.known_to IS NULL
           JOIN ref_metric m ON m.metric_id=s.metric_id
           WHERE s.source_type='supplemental' AND s.retired_at IS NULL AND s.known_to IS NULL AND s.segment='Total'
             AND ABS(s.value - x.value) > 0.01 * GREATEST(ABS(x.value), 1) LIMIT 500"""
    )
    return [
        Finding(
            f"{r['metric_code']} {r['period_type']} {r['period_end']}",
            f"supplemental {r['supp']}",
            f"xbrl {r['xbrl']}",
            r["company_id"],
            {"doc_id": r["source_doc_id"], "page": r["source_page_no"], "accession": r["source_accession"]},
        )
        for r in rows
    ]


# ------------------------------------------------------------------ freshness / ops
@check("freshness.sec_sync", "warn", "Every tracked company had its SEC submissions processed recently")
def sec_fresh(db: Db, ctx: Ctx) -> list[Finding]:
    rows = db.query(
        """SELECT company_id, ticker, submissions_synced_at FROM ref_company WHERE in_universe=1 AND
           (submissions_synced_at IS NULL OR submissions_synced_at < UTC_TIMESTAMP() - INTERVAL :d DAY)""",
        {"d": ctx.sync_max_age_days},
    )
    return [
        Finding(
            f"{r['ticker']} submissions",
            str(r["submissions_synced_at"]),
            f"within {ctx.sync_max_age_days} days",
            r["company_id"],
        )
        for r in rows
    ]


@check("freshness.periodic_filing", "warn", "Every tracked company filed a 10-K or 10-Q recently")
def filing_fresh(db: Db, ctx: Ctx) -> list[Finding]:
    rows = db.query(
        """SELECT c.company_id, c.ticker, MAX(f.filing_date) AS last_filed FROM ref_company c
           JOIN stg_sec_filing f ON f.company_id=c.company_id AND f.form_type IN ('10-K','10-Q','10-K/A','10-Q/A')
           WHERE c.in_universe=1 GROUP BY c.company_id, c.ticker
           HAVING last_filed < UTC_DATE() - INTERVAL :d DAY""",
        {"d": ctx.filing_max_age_days},
    )
    return [
        Finding(
            f"{r['ticker']} last 10-K/10-Q",
            str(r["last_filed"]),
            f"within {ctx.filing_max_age_days} days",
            r["company_id"],
        )
        for r in rows
    ]


@check("freshness.supplemental_overdue", "warn", "Each REIT's next quarterly supplemental has arrived when due")
def supplemental_overdue(db: Db, ctx: Ctx) -> list[Finding]:
    from ..sources.ir.expectations import expected_supplementals

    return [
        Finding(
            f"{e.ticker} supplemental",
            f"latest held {e.latest_period}",
            f"{e.next_period} by {e.due_date}",
            e.company_id,
            {"reason": e.reason, "lag_days": e.lag_days},
        )
        for e in expected_supplementals(db, grace_days=ctx.supplemental_grace_days)
        if e.status == "overdue"
    ]


@check("ops.ir_site_health", "warn", "IR results pages load and still list supplementals")
def ir_site_health(db: Db, ctx: Ctx) -> list[Finding]:
    rows = db.query(
        """SELECT c.company_id, c.ticker, s.url, s.last_outcome, s.consecutive_failures, s.max_link_count
           FROM ref_ir_source s JOIN ref_company c ON c.company_id = s.company_id
           WHERE c.in_universe=1 AND s.is_active=1 AND s.source_type IN ('results_page','supplemental_pdf')
             AND (s.last_outcome='layout_break' OR s.consecutive_failures >= :n)""",
        {"n": ctx.ir_failures_threshold},
    )
    return [
        Finding(
            f"{r['ticker']} {r['url'][:180]}",
            f"{r['last_outcome']}, {r['consecutive_failures']} failures in a row",
            "loads and lists supplementals",
            r["company_id"],
            {"max_link_count": r["max_link_count"]},
        )
        for r in rows
    ]


@check("ops.dead_letters", "warn", "Open items in the dead-letter queue")
def open_dead_letters(db: Db, ctx: Ctx) -> list[Finding]:
    rows = db.query(
        "SELECT stage, COUNT(*) n, MIN(first_seen_at) oldest FROM ops_dead_letter WHERE status='open' GROUP BY stage"
    )
    return [Finding(f"stage {r['stage']}", f"{r['n']} open", "0", details={"oldest": str(r["oldest"])}) for r in rows]


@check("ops.docs_needing_review", "info", "Documents waiting for identification or review")
def docs_review(db: Db, ctx: Ctx) -> list[Finding]:
    rows = db.query(
        "SELECT status, COUNT(*) n FROM stg_document WHERE status IN ('needs_review','failed') GROUP BY status"
    )
    return [Finding(f"documents {r['status']}", str(r["n"]), "0") for r in rows]


# ------------------------------------------------------------------ runner
def _s(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        v = v.normalize()
    return str(v)[:255]


def run_checks(
    db: Db, ctx: Ctx, run_id: int | None, only: list[str] | None = None, max_rows_per_check: int = 200
) -> list[dict[str, Any]]:
    summary = []
    for c in CHECKS:
        if only and not any(c.code.startswith(o) for o in only):
            continue
        try:
            findings = c.fn(db, ctx)
            status = "fail" if findings else "pass"
            err = None
        except Exception as e:
            db.rollback()
            findings, status, err = [], "error", f"{type(e).__name__}: {e}"
        db.execute(
            """INSERT INTO ops_dq_result (run_id, check_code, severity, status, subject, observed, expected, details)
               VALUES (:r, :c, :sev, :st, 'summary', :obs, NULL, :det)""",
            {
                "r": run_id,
                "c": c.code,
                "sev": c.severity,
                "st": status,
                "obs": f"{len(findings)} findings",
                "det": json.dumps({"description": c.description, "error": err}),
            },
        )
        db.executemany(
            """INSERT INTO ops_dq_result (run_id, check_code, severity, status, company_id, subject, observed, expected,
                 details) VALUES (:r, :c, :sev, 'fail', :cid, :sub, :obs, :exp, :det)""",
            (
                {
                    "r": run_id,
                    "c": c.code,
                    "sev": c.severity,
                    "cid": f.company_id,
                    "sub": _s(f.subject),
                    "obs": _s(f.observed),
                    "exp": _s(f.expected),
                    "det": json.dumps(f.details, default=str),
                }
                for f in findings[:max_rows_per_check]
            ),
        )
        db.commit()
        summary.append(
            {
                "check": c.code,
                "severity": c.severity,
                "status": status,
                "findings": len(findings),
                "error": err,
                "sample": [f.subject for f in findings[:3]],
            }
        )
    return summary
