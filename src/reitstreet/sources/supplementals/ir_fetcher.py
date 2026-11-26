"""IR website fetcher.

Built now: download every active ref_ir_source of type supplemental_pdf (direct PDF
links from the universe file), register it as a supplemental, de-duplicate by content.
Polite by design: its own rate limit (default 1 request/second), a real User-Agent,
and every response captured in the raw store.

Next (separate piece of work, see docs/adr/0006): results_page discovery, i.e. read
each company's results page, find new supplemental links per IR platform (Q4,
Notified, Equisolve, WordPress), and register them here.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ...core.db import Db
from ...core.errors import TransientError
from ...core.http import Fetcher
from ..documents import mark_fetched


def fetch_supplemental_pdfs(db: Db, fetcher: Fetcher, company: dict[str, Any], run_id: int | None) -> dict[str, int]:
    out = {"checked": 0, "new_docs": 0, "unchanged": 0, "failed": 0}
    for src in db.query(
        """SELECT source_id, url FROM ref_ir_source WHERE company_id=:c AND source_type='supplemental_pdf'
               AND is_active=1""",
        {"c": company["company_id"]},
    ):
        out["checked"] += 1
        try:
            r = fetcher.get(src["url"], allow_404=True)
        except TransientError:
            db.execute(
                "UPDATE ref_ir_source SET consecutive_failures=consecutive_failures+1, "
                "last_checked_at=CURRENT_TIMESTAMP(6) WHERE source_id=:s",
                {"s": src["source_id"]},
            )
            db.commit()
            out["failed"] += 1
            raise
        if r is None:
            db.execute(
                "UPDATE ref_ir_source SET consecutive_failures=consecutive_failures+1, "
                "last_checked_at=CURRENT_TIMESTAMP(6) WHERE source_id=:s",
                {"s": src["source_id"]},
            )
            out["failed"] += 1
            continue
        changed = r.sha256 != db.scalar(
            "SELECT last_sha256 FROM ref_ir_source WHERE source_id=:s", {"s": src["source_id"]}
        )
        db.execute(
            """UPDATE ref_ir_source SET consecutive_failures=0, last_checked_at=CURRENT_TIMESTAMP(6), last_sha256=:sha,
                 last_changed_at=IF(:ch, CURRENT_TIMESTAMP(6), last_changed_at) WHERE source_id=:s""",
            {"sha": r.sha256, "ch": int(changed), "s": src["source_id"]},
        )
        # Many IR sites reuse one URL for "latest supplemental", so each distinct content at a URL is its
        # own document: the content hash goes in the URL fragment (ignored by servers, kept for lineage).
        doc_id = db.execute(
            """INSERT IGNORE INTO stg_document (company_id, doc_kind, source, source_url, original_filename, mime_type,
                 published_date, registered_run_id)
               VALUES (:c, 'supplemental', 'ir_website', :u, :fn, 'application/pdf', :pd, :run)""",
            {
                "c": company["company_id"],
                "u": f"{src['url'].split('#')[0]}#sha256={r.sha256}",
                "fn": src["url"].split("#")[0].rsplit("/", 1)[-1][:255],
                "pd": date.today(),
                "run": run_id,
            },
        )
        if doc_id:
            mark_fetched(db, doc_id, r.sha256, len(r.body))
            out["new_docs"] += 1
        else:
            out["unchanged"] += 1
        db.commit()
    return out
