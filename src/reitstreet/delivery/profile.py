"""Everything relevant for one company, in one call: `reitstreet company-profile --ticker NNN`.

What is "relevant" is not decided here. It comes from the sector catalog: the company's
sectors (ref_company_sector) pick the metrics that matter (ref_metric_sector), and the
profile lists every one of them with its latest value, or marks it missing. The rest of
the profile is the latest schedule of each record table (debt maturities, debt
instruments, lease expirations, top tenants, development pipeline) and a summary of the
property-level data. Every value carries its source document and page.

The same data is available to clients as the dl_company_profile, dl_property_metrics,
dl_debt_maturities, dl_debt_instruments, dl_lease_expirations, dl_top_tenants and
dl_development_pipeline views (and `reitstreet export`).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..core.db import Db

SECTIONS = {
    "debt_maturities": "dl_debt_maturities",
    "debt_instruments": "dl_debt_instruments",
    "lease_expirations": "dl_lease_expirations",
    "top_tenants": "dl_top_tenants",
    "development_pipeline": "dl_development_pipeline",
}
RELEVANCE_ORDER = {"core": 0, "common": 1, "optional": 2}


def _plain(v: Any) -> Any:
    if isinstance(v, Decimal):
        return format(v.normalize(), "f") if v == v.to_integral() else format(v, "f").rstrip("0").rstrip(".")
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def _rows(db: Db, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [{k: _plain(v) for k, v in r.items()} for r in db.query(sql, params)]


def company_profile(db: Db, ticker: str) -> dict[str, Any] | None:
    company = db.one(
        "SELECT company_id, ticker, cik, name, sector, reit_type, fiscal_year_end FROM ref_company WHERE ticker=:t",
        {"t": ticker.upper()},
    )
    if company is None:
        return None
    p = {"t": company["ticker"]}
    sectors = _rows(
        db,
        """SELECT s.sector_code, s.name, cs.is_primary, cs.origin FROM ref_company_sector cs
           JOIN ref_sector s ON s.sector_code = cs.sector_code
           WHERE cs.company_id = :c ORDER BY cs.is_primary DESC, s.sort_order""",
        {"c": company["company_id"]},
    )
    metrics = sorted(
        _rows(db, "SELECT * FROM dl_company_profile WHERE ticker = :t", p),
        key=lambda r: (RELEVANCE_ORDER.get(r["relevance"], 9), r["category"], r["metric_code"]),
    )
    for r in metrics:
        r["has_value"] = bool(r["has_value"])
    found = [r for r in metrics if r["has_value"]]
    core = [r for r in metrics if r["relevance"] == "core"]
    props = db.one(
        """SELECT COUNT(DISTINCT property_name, COALESCE(city, ''), COALESCE(state, '')) AS properties,
                  COUNT(*) AS values_, MAX(as_of_date) AS latest, COUNT(DISTINCT property_id) AS matched
           FROM dl_property_metrics WHERE ticker = :t""",
        p,
    )
    out: dict[str, Any] = {
        "company": {k: _plain(v) for k, v in company.items() if k != "company_id"},
        "sectors": sectors,
        "coverage": {
            "relevant_metrics": len(metrics),
            "with_value": len(found),
            "core_metrics": len(core),
            "core_with_value": sum(1 for r in core if r["has_value"]),
        },
        "metrics": metrics,
        "properties": {
            "count": int(props["properties"]) if props else 0,
            "values": int(props["values_"]) if props else 0,
            "matched_to_property_master": int(props["matched"]) if props else 0,
            "latest_as_of": _plain(props["latest"]) if props else None,
        },
    }
    for name, view in SECTIONS.items():
        out[name] = _rows(db, f"SELECT * FROM {view} WHERE ticker = :t", p)
    return out


def format_profile(prof: dict[str, Any]) -> str:
    """Plain-text rendering for the terminal."""
    c = prof["company"]
    lines = [f"{c['ticker']}  {c['name']}  (CIK {c['cik']})"]
    secs = ", ".join(s["name"] + (" (primary)" if s["is_primary"] else "") for s in prof["sectors"]) or "none mapped"
    lines.append(f"sectors: {secs}")
    cv = prof["coverage"]
    lines.append(
        f"metrics: {cv['with_value']} of {cv['relevant_metrics']} relevant have values; "
        f"core {cv['core_with_value']} of {cv['core_metrics']}"
    )
    lines.append("")
    cat = None
    for r in prof["metrics"]:
        if r["category"] != cat:
            cat = r["category"]
            lines.append(f"[{cat}]")
        if r["has_value"]:
            seg = "" if r["segment"] in (None, "Total") else f" {r['segment']}"
            page = f" p.{r['source_page_no']}" if r["source_page_no"] else ""
            lines.append(
                f"  {r['metric_code']:<26}{r['value']:>18} {r['unit'] or ''} {r['period_type']} {r['period_end']}"
                f"{seg}  {r['source_type']}{page}"
            )
        else:
            lines.append(f"  {r['metric_code']:<26}{'missing':>18}  ({r['relevance']})")
    pr = prof["properties"]
    lines += [
        "",
        f"properties: {pr['count']} with {pr['values']} values, latest {pr['latest_as_of']}, "
        f"{pr['matched_to_property_master']} matched",
    ]
    for name in SECTIONS:
        rows = prof[name]
        as_of = rows[0]["as_of_date"] if rows else None
        lines.append(f"{name.replace('_', ' ')}: {len(rows)} rows" + (f" as of {as_of}" if as_of else ""))
    return "\n".join(lines)
