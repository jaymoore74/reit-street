"""Parse coverage by company: `reitstreet parse-coverage`.

For each company: supplementals stored, how many parsed cleanly, how many need review
or failed, the share of values and records sent to quarantine, how many of the metrics
the sector catalog calls core have a live value, and the latest as-of date of each
schedule (debt maturities, lease expirations, tenants, development, property facts).
This is the number to watch as parsers improve; `parse-eval` gives the same view for a
folder of PDFs before they are imported.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..core.db import Db

COVERAGE_SQL = """
SELECT c.company_id, c.ticker, c.sector,
  (SELECT COUNT(*) FROM stg_document d WHERE d.company_id = c.company_id
      AND d.doc_kind = 'supplemental') AS supplementals,
  (SELECT COUNT(*) FROM stg_document d WHERE d.company_id = c.company_id
      AND d.doc_kind = 'supplemental' AND d.status = 'parsed') AS parsed,
  (SELECT COUNT(*) FROM stg_document d WHERE d.company_id = c.company_id
      AND d.doc_kind = 'supplemental' AND d.status = 'needs_review') AS needs_review,
  (SELECT COUNT(*) FROM stg_document d WHERE d.company_id = c.company_id
      AND d.doc_kind = 'supplemental' AND d.status = 'failed') AS failed,
  (SELECT MAX(d.period_end_date) FROM stg_document d WHERE d.company_id = c.company_id
      AND d.doc_kind = 'supplemental' AND d.status = 'parsed') AS latest_parsed_period,
  (SELECT COALESCE(SUM(p.n_items), 0) FROM stg_doc_parse p JOIN stg_document d ON p.parse_id = d.current_parse_id
      WHERE d.company_id = c.company_id) AS items,
  (SELECT COALESCE(SUM(p.n_quarantined), 0) FROM stg_doc_parse p JOIN stg_document d ON p.parse_id = d.current_parse_id
      WHERE d.company_id = c.company_id) AS quarantined,
  (SELECT COALESCE(SUM(p.n_records), 0) FROM stg_doc_parse p JOIN stg_document d ON p.parse_id = d.current_parse_id
      WHERE d.company_id = c.company_id) AS records,
  (SELECT COUNT(*) FROM dl_company_profile v WHERE v.ticker = c.ticker AND v.relevance = 'core') AS core_metrics,
  (SELECT COUNT(*) FROM dl_company_profile v WHERE v.ticker = c.ticker AND v.relevance = 'core'
      AND v.has_value = 1) AS core_with_value,
  (SELECT COUNT(*) FROM dl_company_profile v WHERE v.ticker = c.ticker AND v.relevance = 'core'
      AND v.has_value = 1 AND v.source_type = 'supplemental') AS core_from_supplemental,
  (SELECT MAX(as_of_date) FROM wh_debt_maturity x WHERE x.company_id = c.company_id AND x.is_live = 1)
      AS debt_maturities_as_of,
  (SELECT MAX(as_of_date) FROM wh_lease_expiration x WHERE x.company_id = c.company_id AND x.is_live = 1)
      AS lease_expirations_as_of,
  (SELECT MAX(as_of_date) FROM wh_tenant_exposure x WHERE x.company_id = c.company_id AND x.is_live = 1)
      AS top_tenants_as_of,
  (SELECT MAX(as_of_date) FROM wh_development_project x WHERE x.company_id = c.company_id AND x.is_live = 1)
      AS development_as_of,
  (SELECT MAX(as_of_date) FROM wh_property_metric x WHERE x.company_id = c.company_id AND x.is_live = 1)
      AS property_metrics_as_of
FROM ref_company c
WHERE c.in_universe = 1 {where}
ORDER BY c.ticker
"""


def _plain(v: Any) -> Any:
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral() else float(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def coverage_rows(db: Db, tickers: list[str] | None = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    where = ""
    if tickers:
        names = [f"t{i}" for i in range(len(tickers))]
        where = "AND c.ticker IN (" + ", ".join(":" + n for n in names) + ")"
        params = {n: t.upper() for n, t in zip(names, tickers, strict=True)}
    rows = []
    for r in db.query(COVERAGE_SQL.format(where=where), params):
        d = {k: _plain(v) for k, v in r.items() if k != "company_id"}
        d["quarantine_pct"] = round(100 * d["quarantined"] / d["items"], 1) if d["items"] else None
        d["core_pct"] = round(100 * d["core_with_value"] / d["core_metrics"], 1) if d["core_metrics"] else None
        rows.append(d)
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    with_supp = [r for r in rows if r["supplementals"]]
    core = sum(r["core_metrics"] for r in rows)
    items = sum(r["items"] for r in rows)
    return {
        "companies": len(rows),
        "with_supplementals": len(with_supp),
        "with_parsed_supplemental": sum(1 for r in rows if r["parsed"]),
        "supplementals": sum(r["supplementals"] for r in rows),
        "parsed": sum(r["parsed"] for r in rows),
        "needs_review": sum(r["needs_review"] for r in rows),
        "failed": sum(r["failed"] for r in rows),
        "values": items,
        "quarantine_pct": round(100 * sum(r["quarantined"] for r in rows) / items, 1) if items else None,
        "records": sum(r["records"] for r in rows),
        "core_metric_pct": round(100 * sum(r["core_with_value"] for r in rows) / core, 1) if core else None,
        "with_debt_maturities": sum(1 for r in rows if r["debt_maturities_as_of"]),
        "with_lease_expirations": sum(1 for r in rows if r["lease_expirations_as_of"]),
        "with_top_tenants": sum(1 for r in rows if r["top_tenants_as_of"]),
        "with_development": sum(1 for r in rows if r["development_as_of"]),
        "with_property_metrics": sum(1 for r in rows if r["property_metrics_as_of"]),
    }


def write_csv(rows: list[dict[str, Any]], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        if not rows:
            return
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
