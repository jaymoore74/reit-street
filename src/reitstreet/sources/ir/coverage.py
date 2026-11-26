"""Per-company IR coverage: what the fetcher can do for each REIT today.

coverage (from view ops_ir_coverage):
  works           supplementals listed and fetched by the current strategies
  needs_headless  page lists documents only after JavaScript runs (most Q4 Inc sites)
  no_supp         site reachable, no quarterly supplemental published there
  blocked         robots.txt disallows us
  broken          HTTP errors or a layout break; see ops_ir_check.note
  not_probed / error / no_source   nothing reliable known yet
Before the first live run, the coverage column falls back to the manual probe in
ir_profiles.csv (probe_status).
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import Any

from ...core.db import Db

COLUMNS = [
    "ticker", "name", "reit_type", "coverage", "strategy", "platform", "expects_supplemental", "probe_status",
    "probe_supp_links", "probe_latest_period", "last_checked_at", "last_strategy", "last_outcome", "last_http_status",
    "last_supp_links", "last_page_supp_period", "latest_supplemental_held",
]  # fmt: skip


def coverage_rows(db: Db) -> list[dict[str, Any]]:
    return db.query(f"SELECT {', '.join(COLUMNS)} FROM ops_ir_coverage ORDER BY ticker")


def summarize(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(r["coverage"]) for r in rows).most_common())


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r[k]) for k in COLUMNS})
