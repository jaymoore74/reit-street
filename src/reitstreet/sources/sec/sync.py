"""SEC EDGAR sync for one company: submissions -> filing index pages -> exhibits ->
companyfacts. Network in, raw store + staging out. Parse failures go to the
dead-letter queue and the rest of the company still loads."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ...core.db import Db
from ...core.errors import ParseError
from ...core.http import Fetcher
from ...core.jobs import dead_letter
from ...parsers import sec_companyfacts, sec_index, sec_submissions
from ..documents import fetch_document
from . import loaders, urls

log = logging.getLogger(__name__)


@dataclass
class SyncOptions:
    full_history: bool = False
    force: bool = False
    index_docs: bool = True
    download_exhibits: bool = True
    doc_limit: int | None = None
    facts: bool = True


@dataclass
class SyncResult:
    new_filings: int = 0
    indexed: int = 0
    new_docs: int = 0
    downloaded: int = 0
    facts_status: str = "skipped"
    new_facts: int = 0
    parse_errors: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def rows(self) -> int:
        return self.new_filings + self.new_docs + self.new_facts

    @property
    def warehouse_dirty(self) -> bool:
        return self.new_facts > 0 or self.facts_status == "loaded"


def _parse_fail(
    db: Db, run_id: int | None, stage: str, key: str, sha: str, e: Exception, cid: int, res: SyncResult
) -> None:
    db.rollback()
    res.parse_errors += 1
    res.notes.append(f"{stage}: {e}")
    dead_letter(db, stage=stage, item_key=key, exc=e, run_id=run_id, sha256=sha, company_id=cid)


def sync_company(
    db: Db,
    f: Fetcher,
    company: dict[str, Any],
    opt: SyncOptions,
    run_id: int | None,
    cache: loaders.ConceptCache | None = None,
) -> SyncResult:
    res = SyncResult()
    cid, cik = company["company_id"], int(company["cik"])
    key = f"cik:{cik}"

    # 1. submissions
    r = f.get(urls.submissions_url(cik))
    assert r is not None
    if opt.force or r.sha256 != company.get("submissions_sha256"):
        try:
            sub = sec_submissions.parse_submissions(r.body)
            res.new_filings += loaders.load_submissions(db, company, r.sha256, sub, run_id)
            db.commit()
            have_pages = db.scalar("SELECT COUNT(*) FROM stg_sec_filing WHERE company_id=:c", {"c": cid})
            if opt.full_history or have_pages <= len(sub.filings):
                for name in sub.page_files:
                    pr = f.get(urls.submissions_page_url(name))
                    assert pr is not None
                    try:
                        rows = sec_submissions.parse_submissions_page(pr.body)
                        res.new_filings += loaders.load_filings(db, cid, cik, pr.sha256, rows, run_id)
                        db.commit()
                    except ParseError as e:
                        _parse_fail(db, run_id, "sec.submissions_page.parse", f"{key}:{name}", pr.sha256, e, cid, res)
        except ParseError as e:
            _parse_fail(db, run_id, "sec.submissions.parse", key, r.sha256, e, cid, res)

    # 2. filing index pages and 8-K exhibits
    if opt.index_docs:
        todo = [
            x
            for x in db.query(
                """SELECT filing_id, accession_no, form_type, items, filing_date, report_date FROM stg_sec_filing
               WHERE company_id=:c AND docs_indexed_at IS NULL ORDER BY filing_date DESC""",
                {"c": cid},
            )
            if sec_index.wants_index(x["form_type"], x["items"])
        ]
        for filing in todo[: opt.doc_limit] if opt.doc_limit else todo:
            ir = f.get(urls.filing_index_url(cik, filing["accession_no"]), allow_404=True)
            if ir is None:
                db.execute(
                    "UPDATE stg_sec_filing SET docs_indexed_at=CURRENT_TIMESTAMP(6) WHERE filing_id=:f",
                    {"f": filing["filing_id"]},
                )
                db.commit()
                continue
            try:
                docs = sec_index.parse_index_html(ir.text())
                if not docs:
                    raise ParseError("index page has no Document Format Files table")
                new_ids = loaders.load_index(db, company, filing, ir.sha256, docs, run_id)
                db.commit()
            except ParseError as e:
                _parse_fail(db, run_id, "sec.index.parse", f"{key}:{filing['accession_no']}", ir.sha256, e, cid, res)
                continue
            res.indexed += 1
            res.new_docs += len(new_ids)
        if opt.download_exhibits:
            for doc in db.query(
                """SELECT doc_id, source_url FROM stg_document WHERE company_id=:c AND source='sec_8k_exhibit'
                       AND status='registered' AND doc_kind IN ('supplemental','earnings_release','presentation')
                       ORDER BY published_date DESC""",
                {"c": cid},
            ):
                if fetch_document(db, f, doc) == "fetched":
                    res.downloaded += 1
                db.commit()

    # 3. XBRL companyfacts
    if opt.facts:
        cr = f.get(urls.companyfacts_url(cik), allow_404=True)
        if cr is None:
            res.facts_status = "no_xbrl"
        elif not opt.force and cr.sha256 == company.get("facts_sha256"):
            res.facts_status = "unchanged"
        else:
            try:
                concepts, facts = sec_companyfacts.parse_companyfacts(cr.body)
                res.new_facts = loaders.load_companyfacts(db, company, cr.sha256, concepts, facts, run_id, cache)
                db.commit()
                res.facts_status = "loaded"
            except ParseError as e:
                _parse_fail(db, run_id, "sec.companyfacts.parse", key, cr.sha256, e, cid, res)
    return res
