"""Property coverage per REIT: what the source map promises vs what the master holds."""

from __future__ import annotations

from typing import Any

from ..core.db import Db

SQL = """
SELECT c.ticker, c.sector, c.reit_type,
  (SELECT GROUP_CONCAT(DISTINCT CONCAT(s.source_kind, ':', s.lists_properties, ':', s.status)
          ORDER BY s.source_kind SEPARATOR ' ')
     FROM ref_property_source s WHERE s.company_id = c.company_id) AS sources,
  (SELECT MAX(s.approx_property_count) FROM ref_property_source s WHERE s.company_id = c.company_id) AS stated_count,
  (SELECT COUNT(*) FROM wh_property p WHERE p.owner_company_id = c.company_id AND p.status = 'active') AS properties,
  (SELECT COUNT(*) FROM wh_property p WHERE p.owner_company_id = c.company_id AND p.status = 'active'
      AND p.latitude IS NOT NULL) AS located,
  (SELECT MAX(r.as_of_date) FROM stg_property_record r
     WHERE r.company_id = c.company_id AND r.retired_at IS NULL) AS latest_as_of,
  (SELECT COUNT(*) FROM stg_property_record r WHERE r.company_id = c.company_id AND r.retired_at IS NULL
      AND r.source_kind = 'sec_schedule_iii' AND r.is_aggregate = 1
      AND r.source_property_ref <> '__total__') AS sched_iii_aggregate_rows
FROM ref_company c
WHERE c.in_universe = 1 {where}
ORDER BY c.ticker
"""


def coverage(db: Db, *, equity_only: bool = True) -> list[dict[str, Any]]:
    rows = db.query(
        SQL.format(where="AND (c.reit_type IS NULL OR LOWER(c.reit_type) <> 'mortgage')" if equity_only else "")
    )
    for r in rows:
        n, stated = int(r["properties"] or 0), r["stated_count"]
        r["coverage_pct"] = round(100.0 * n / int(stated), 1) if stated else None
    return rows


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    with_props = [r for r in rows if r["properties"]]
    stated = [r for r in rows if r["stated_count"]]
    return {
        "companies": len(rows),
        "companies_with_properties": len(with_props),
        "properties": sum(int(r["properties"] or 0) for r in rows),
        "located": sum(int(r["located"] or 0) for r in rows),
        "stated_total": sum(int(r["stated_count"]) for r in stated),
        "companies_80pct_plus": sum(1 for r in stated if (r["coverage_pct"] or 0) >= 80),
    }
