"""Direct supplemental PDF URLs (the latest_supplemental_pdf_url column of the universe file).

Many IR sites reuse one URL for "latest supplemental", so each distinct content seen at a
URL is its own document: the content hash goes in the URL fragment (ignored by servers,
kept for lineage). Requests are conditional (ETag / Last-Modified), so an unchanged file
is not downloaded again, and robots.txt is honored.

Results pages (lists of documents) are handled by sources.ir.fetch.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse

from ...core.db import Db
from ...core.errors import TransientError
from ...core.http import Fetcher
from ..documents import mark_fetched

if TYPE_CHECKING:
    from ..ir.robots import RobotsCache


def fetch_supplemental_pdfs(
    db: Db, fetcher: Fetcher, company: dict[str, Any], run_id: int | None, robots: RobotsCache | None = None
) -> dict[str, int]:
    from ..ir.classify import link_period
    from ..ir.pdfmeta import pdf_title_and_period

    out = {"checked": 0, "new_docs": 0, "unchanged": 0, "failed": 0, "robots_blocked": 0}
    for src in db.query(
        """SELECT source_id, url, http_etag, http_last_modified FROM ref_ir_source
           WHERE company_id=:c AND source_type='supplemental_pdf' AND is_active=1""",
        {"c": company["company_id"]},
    ):
        out["checked"] += 1
        url = src["url"].split("#")[0]
        if robots is not None and not robots.allowed(url):
            out["robots_blocked"] += 1
            _mark(db, src["source_id"], "robots_blocked", failed=False)
            continue
        try:
            r = fetcher.get(url, allow_404=True, etag=src["http_etag"], last_modified=src["http_last_modified"])
        except TransientError:
            _mark(db, src["source_id"], "http_error", failed=True)
            db.commit()
            out["failed"] += 1
            raise
        if r is None:
            _mark(db, src["source_id"], "http_error", failed=True)
            out["failed"] += 1
            continue
        if r.not_modified:
            _mark(db, src["source_id"], "not_modified", failed=False)
            out["unchanged"] += 1
            db.commit()
            continue
        changed = r.sha256 != db.scalar(
            "SELECT last_sha256 FROM ref_ir_source WHERE source_id=:s", {"s": src["source_id"]}
        )
        db.execute(
            """UPDATE ref_ir_source SET consecutive_failures=0, last_checked_at=CURRENT_TIMESTAMP(6), last_sha256=:sha,
                 last_outcome='ok', http_etag=:et, http_last_modified=:lm,
                 last_changed_at=IF(:ch, CURRENT_TIMESTAMP(6), last_changed_at) WHERE source_id=:s""",
            {
                "sha": r.sha256,
                "ch": int(changed),
                "s": src["source_id"],
                "et": (r.headers.get("etag") or "")[:255] or None,
                "lm": (r.headers.get("last-modified") or "")[:64] or None,
            },
        )
        fname = unquote(urlparse(url).path.rsplit("/", 1)[-1])
        fye = company.get("fiscal_year_end")
        period = link_period("", url, "", fye)
        title = None
        if period is None:
            title, period = pdf_title_and_period(r.body, fye)
        doc_id = db.execute(
            """INSERT IGNORE INTO stg_document (company_id, doc_kind, kind_source, source, source_url,
                 original_filename, title, mime_type, period_end_date, period_source, published_date, registered_run_id)
               VALUES (:c, 'supplemental', 'universe_file', 'ir_website', :u, :fn, :ti, 'application/pdf', :pe, :ps,
                 :pd, :run)""",
            {
                "c": company["company_id"],
                "u": f"{url}#sha256={r.sha256}",
                "fn": fname[:255] or None,
                "ti": title,
                "pe": period.period_end if period else None,
                "ps": period.source if period else None,
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


def _mark(db: Db, source_id: int, outcome: str, *, failed: bool) -> None:
    db.execute(
        """UPDATE ref_ir_source SET last_checked_at=CURRENT_TIMESTAMP(6), last_outcome=:o,
             consecutive_failures=IF(:f, consecutive_failures+1, 0) WHERE source_id=:s""",
        {"o": outcome, "f": int(failed), "s": source_id},
    )
