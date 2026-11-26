"""Client delivery: CSV files written from the dl_ views, plus a manifest.

Three time modes for metrics:
  current (default)   latest knowledge, live rows: dl_metrics
  --as-of-date D      what a client would have known on date D (knowledge time): the
                      version with known_from <= D < known_to. Backtests use this; it
                      has no look-ahead bias.
  --as-recorded T     what our warehouse contained at system time T (audit: "what did we
                      deliver on that day?"), including rows later retired by a rebuild.

Every export writes manifest.json with row counts, SHA-256 of each file, the code version
and the arguments, so a delivered file can always be traced and re-produced.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..core.db import Db, in_clause
from ..core.jobs import code_version

VIEWS = {
    "companies": "dl_companies",
    "metrics": "dl_metrics",
    "metrics_history": "dl_metrics_history",
    "filings": "dl_filings",
    "documents": "dl_documents",
    "metric_catalog": "dl_metric_catalog",
    "company_profile": "dl_company_profile",
    "property_metrics": "dl_property_metrics",
    "debt_maturities": "dl_debt_maturities",
    "debt_instruments": "dl_debt_instruments",
    "lease_expirations": "dl_lease_expirations",
    "top_tenants": "dl_top_tenants",
    "development_pipeline": "dl_development_pipeline",
}
NO_TICKER = {"metric_catalog"}

POINT_IN_TIME_SQL = """
SELECT c.ticker, c.cik, m.metric_code, m.name AS metric_name, m.category, v.period_type, v.period_start,
       v.period_end, v.fiscal_year, v.fiscal_quarter, v.segment, v.value, v.unit, v.source_type, v.version,
       v.known_from AS published_date, v.known_to AS superseded_date, v.source_accession, v.source_doc_id,
       v.source_page_no, v.value_id
FROM wh_metric_value v
JOIN ref_company c ON c.company_id = v.company_id
JOIN ref_metric m ON m.metric_id = v.metric_id
WHERE {where}"""


def _fmt(v: Any) -> Any:
    if isinstance(v, Decimal):
        return format(v.normalize(), "f")
    if isinstance(v, datetime | date):
        return v.isoformat()
    return v


def _filters(
    name: str, tickers: list[str] | None, metrics: list[str] | None, since: date | None, prefix: str = ""
) -> tuple[list[str], dict[str, Any]]:
    where: list[str] = []
    params: dict[str, Any] = {}
    if tickers and name not in NO_TICKER:
        c, p = in_clause("tk", tickers)
        where.append(f"{prefix}ticker IN {c}")
        params.update(p)
    if metrics and (name.startswith("metrics") or name in ("company_profile", "property_metrics", "metric_catalog")):
        c, p = in_clause("mc", metrics)
        where.append(f"{prefix.replace('c.', 'm.')}metric_code IN {c}")
        params.update(p)
    if since and name.startswith("metrics"):
        where.append(f"{prefix.replace('c.', 'v.')}period_end >= :since")
        params["since"] = since
    return where, params


def _write(db: Db, sql: str, params: dict[str, Any], path: Path) -> tuple[int, str]:
    h = hashlib.sha256()
    n = 0
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        cols: list[str] | None = None
        for row in db.iter(sql, params):
            if cols is None:
                cols = list(row.keys())
                w.writerow(cols)
            w.writerow([_fmt(row[c]) for c in cols])
            n += 1
        if cols is None:
            w.writerow(["no rows"])
    tmp.replace(path)
    h.update(path.read_bytes())
    return n, h.hexdigest()


def export(
    db: Db,
    out_dir: Path,
    *,
    views: list[str] | None = None,
    tickers: list[str] | None = None,
    metrics: list[str] | None = None,
    since: date | None = None,
    as_of_date: date | None = None,
    as_recorded: datetime | None = None,
) -> dict[str, Any]:
    if as_of_date and as_recorded:
        raise ValueError("use --as-of-date or --as-recorded, not both")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = Path(out_dir) / stamp
    out.mkdir(parents=True, exist_ok=False)
    files: dict[str, Any] = {}
    for name in views or list(VIEWS):
        if name not in VIEWS:
            raise ValueError(f"unknown view {name}; choose from {', '.join(VIEWS)}")
        if name == "metrics" and (as_of_date or as_recorded):
            where, params = _filters(name, tickers, metrics, since, prefix="c.")
            if as_of_date:
                where += ["v.retired_at IS NULL", "v.known_from <= :asof", "(v.known_to IS NULL OR v.known_to > :asof)"]
                params["asof"] = as_of_date
            else:
                # live at system time T and the latest version as it stood then; a row whose known_to
                # was later filled by a restatement was retired and replaced, so the old row still has NULL
                where += [
                    "v.recorded_at <= :rec",
                    "(v.retired_at IS NULL OR v.retired_at > :rec)",
                    "v.known_to IS NULL",
                ]
                params["rec"] = as_recorded
            sql = POINT_IN_TIME_SQL.format(where=" AND ".join(where))
        else:
            where, params = _filters(name, tickers, metrics, since)
            sql = f"SELECT * FROM {VIEWS[name]}" + (" WHERE " + " AND ".join(where) if where else "")
        path = out / f"{name}.csv"
        n, sha = _write(db, sql, params, path)
        files[name] = {"file": path.name, "rows": n, "sha256": sha}
    manifest = {
        "created_at": stamp,
        "code_version": code_version(),
        "alembic_revision": db.scalar("SELECT version_num FROM alembic_version"),
        "args": {
            "views": views,
            "tickers": tickers,
            "metrics": metrics,
            "since": since,
            "as_of_date": as_of_date,
            "as_recorded": as_recorded,
        },
        "files": files,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return {"dir": str(out), **manifest}
