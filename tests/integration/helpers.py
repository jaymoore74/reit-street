from __future__ import annotations

from pathlib import Path
from typing import Any

from reitstreet.cli import main
from reitstreet.core.db import Db


def cli(*args: str) -> int:
    return main(list(args))


def bootstrap(sec_root: Path, *extra_sync: str) -> None:
    assert cli("migrate") == 0
    assert cli("universe") == 0
    assert cli("sec-sync", "--offline-fixtures", str(sec_root), "--full-history", *extra_sync) == 0


def metric(db: Db, ticker: str, code: str, ptype: str, end: str, source: str = "xbrl") -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in db.query(
            """SELECT v.* FROM wh_metric_value v JOIN ref_company c USING (company_id)
               JOIN ref_metric m USING (metric_id)
               WHERE c.ticker=:t AND m.metric_code=:m AND v.period_type=:p AND v.period_end=:e
                 AND v.source_type=:s AND v.retired_at IS NULL ORDER BY v.version""",
            {"t": ticker, "m": code, "p": ptype, "e": end, "s": source},
        )
    ]


def last_run(db: Db, job: str) -> dict[str, Any]:
    r = db.one("SELECT * FROM ops_job_run WHERE job=:j ORDER BY run_id DESC LIMIT 1", {"j": job})
    assert r is not None
    return dict(r)
