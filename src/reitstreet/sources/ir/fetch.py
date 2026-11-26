"""IR fetch orchestration, one company at a time.

For each company:
  1. direct supplemental PDF URLs from the universe file (supplementals.ir_fetcher)
  2. each active results page through its strategy chain (strategies.run_chain)
  3. every document link seen is upserted into stg_ir_link (first seen = discovery time)
  4. links not downloaded yet, of the kinds asked for, are downloaded newest period first,
     up to max_new_docs per company per run (the rest wait for the next run, so the first
     run on a site with 20 years of history does not hammer it)
  5. one ops_ir_check row per page, and the page's state on ref_ir_source (ETag,
     Last-Modified, link counts, consecutive failures)

Published date of a discovered document is the day we first saw it. For history found on
a first visit that is later than the real publish date, which is the safe direction for
point-in-time data (never earlier than the market could know). The SEC 8-K copy, when it
exists, carries the true filing date and wins de-duplication as the earlier document.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import unquote, urlparse

from ...core.db import Db
from ...core.errors import TransientError
from ...core.http import Fetcher
from ..documents import mark_fetched
from ..supplementals.ir_fetcher import fetch_supplemental_pdfs
from . import models as M
from .models import Discovery, Link, Period
from .pdfmeta import pdf_title_and_period
from .robots import RobotsCache
from .strategies import Page, StrategyEnv, chain, run_chain

log = logging.getLogger(__name__)

MIME = {b"%PDF": "application/pdf", b"PK\x03\x04": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        b"\xd0\xcf\x11\xe0": "application/vnd.ms-excel"}  # fmt: skip
FAILURE_OUTCOMES = {M.HTTP_ERROR, M.LAYOUT_BREAK}


@dataclass
class IrOptions:
    download: bool = True
    kinds: frozenset[str] = frozenset({"supplemental"})
    max_new_docs: int = 40
    backfill: bool = False
    strategy: str | None = None  # force one strategy for every company (diagnostics)
    headless: bool = False
    conditional: bool = True
    layout_break_min_links: int = 3


@dataclass
class CompanyResult:
    pages: int = 0
    outcomes: list[str] = field(default_factory=list)
    links: int = 0
    supp_links: int = 0
    new_links: int = 0
    new_docs: int = 0
    failed_docs: int = 0
    direct: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "pages": self.pages,
            "outcomes": self.outcomes,
            "links": self.links,
            "supp_links": self.supp_links,
            "new_links": self.new_links,
            "new_docs": self.new_docs,
            "failed_docs": self.failed_docs,
            "direct_pdf": self.direct,
        }


def sniff_mime(body: bytes) -> str | None:
    return next((m for magic, m in MIME.items() if body.startswith(magic)), None)


def fetch_company(
    db: Db,
    fetcher: Fetcher,
    robots: RobotsCache,
    company: dict[str, Any],
    run_id: int | None,
    opts: IrOptions,
    q4_key: str | None = None,
) -> CompanyResult:
    res = CompanyResult()
    cid = company["company_id"]
    fye = company.get("fiscal_year_end") or "1231"
    res.direct = fetch_supplemental_pdfs(db, fetcher, company, run_id, robots=robots)
    res.new_docs += res.direct.get("new_docs", 0)
    prof = db.one("SELECT strategy FROM ref_ir_profile WHERE company_id=:c", {"c": cid}) or {}
    strategy = opts.strategy or prof.get("strategy")
    pages = db.query(
        """SELECT source_id, url, http_etag, http_last_modified, last_link_count, max_link_count FROM ref_ir_source
           WHERE company_id=:c AND source_type='results_page' AND is_active=1 ORDER BY source_id""",
        {"c": cid},
    )
    order = chain(strategy, q4_key=q4_key)
    if not pages or not order:
        why = "no results page in the universe file" if not pages else f"strategy {strategy}: no website crawl"
        _log_check(db, run_id, cid, None, strategy or "none", Discovery(strategy or "none", M.NO_SOURCE, note=why), 0)
        res.outcomes.append(M.NO_SOURCE)
        return res
    budget = [opts.max_new_docs if not opts.backfill else 10**6]
    env = StrategyEnv(db, fetcher, robots, headless_enabled=opts.headless, q4_key=q4_key, conditional=opts.conditional)
    for pg in pages:
        res.pages += 1
        page = Page(cid, company["ticker"], pg["source_id"], pg["url"], fye, pg["http_etag"], pg["http_last_modified"])
        d = run_chain(env, page, order)
        prev_max = int(pg["max_link_count"] or 0)
        # An unchanged page (304) still lists what it listed last time.
        supp = int(pg["last_link_count"] or 0) if d.outcome == M.NOT_MODIFIED else len(d.supplemental_links)
        if d.outcome in (M.OK, M.NO_DOCUMENTS, M.JS_ONLY) and supp == 0 and prev_max >= opts.layout_break_min_links:
            d.note = (f"page used to list {prev_max} supplementals and now lists none ({d.outcome}). "
                      f"{d.note}").strip()  # fmt: skip
            d.outcome = M.LAYOUT_BREAK
        new_links, candidates = _upsert_links(db, cid, pg["source_id"], d, run_id)
        if d.outcome == M.NOT_MODIFIED:
            candidates = _backlog(db, cid, pg["source_id"])
        new_docs = failed = 0
        if opts.download and candidates:
            new_docs, failed = _download(db, fetcher, robots, company, candidates, opts, run_id, budget)
        _update_source(db, pg["source_id"], d, supp)
        _log_check(db, run_id, cid, pg["source_id"], d.strategy, d, supp, len(d.links), new_links, new_docs)
        res.outcomes.append(d.outcome)
        res.links += len(d.links)
        res.supp_links += supp
        res.new_links += new_links
        res.new_docs += new_docs
        res.failed_docs += failed
        db.commit()
    return res


# ------------------------------------------------------------------ links
def _upsert_links(
    db: Db, cid: int, source_id: int, d: Discovery, run_id: int | None
) -> tuple[int, list[tuple[int, Link]]]:
    """Returns (links never seen before, links eligible for download: (link_id, link))."""
    new = 0
    eligible: list[tuple[int, Link]] = []
    for lk in d.links:
        row = db.one(
            "SELECT link_id, status FROM stg_ir_link WHERE company_id=:c AND url_hash=SHA2(:u, 256)",
            {"c": cid, "u": lk.url},
        )
        p = lk.period
        params = {
            "c": cid,
            "s": source_id,
            "u": lk.url,
            "t": lk.text[:512] or None,
            "ctx": lk.context[:512] or None,
            "k": lk.kind,
            "kr": lk.kind_reason[:64],
            "fy": p.fiscal_year if p else None,
            "fq": p.fiscal_quarter if p else None,
            "pe": p.period_end if p else None,
            "ps": p.source if p else None,
            "st": d.strategy,
            "run": run_id,
        }
        if row is None:
            link_id = db.execute(
                """INSERT INTO stg_ir_link (company_id, source_id, url, anchor_text, context_text, doc_kind,
                     kind_reason, fiscal_year, fiscal_quarter, period_end_date, period_source, strategy, first_run_id)
                   VALUES (:c, :s, :u, :t, :ctx, :k, :kr, :fy, :fq, :pe, :ps, :st, :run)""",
                params,
            )
            new += 1
            status = "new"
        else:
            link_id, status = row["link_id"], row["status"]
            # Re-classify links not downloaded yet, so a better rule applies to the backlog.
            db.execute(
                """UPDATE stg_ir_link SET last_seen_at=CURRENT_TIMESTAMP(6),
                     doc_kind=IF(status IN ('new','skipped'), :k, doc_kind),
                     kind_reason=IF(status IN ('new','skipped'), :kr, kind_reason),
                     fiscal_year=COALESCE(fiscal_year, :fy), fiscal_quarter=COALESCE(fiscal_quarter, :fq),
                     period_end_date=COALESCE(period_end_date, :pe), period_source=COALESCE(period_source, :ps)
                   WHERE link_id=:id""",
                {**params, "id": link_id},
            )
        if status in ("new", "failed"):
            eligible.append((link_id, lk))
    return new, eligible


def _backlog(db: Db, cid: int, source_id: int) -> list[tuple[int, Link]]:
    """Links from an unchanged page that still wait for download (over last run's budget)."""
    out = []
    for r in db.query(
        """SELECT link_id, url, anchor_text, context_text, doc_kind, kind_reason, fiscal_year, fiscal_quarter,
                  period_end_date, period_source FROM stg_ir_link
           WHERE company_id=:c AND source_id=:s AND status IN ('new','failed')""",
        {"c": cid, "s": source_id},
    ):
        p = (
            Period(r["fiscal_year"], r["fiscal_quarter"], r["period_end_date"], r["period_source"] or "")
            if r["period_end_date"]
            else None
        )
        lk = Link(r["url"], r["anchor_text"] or "", r["context_text"] or "", r["doc_kind"], r["kind_reason"] or "", p)
        out.append((r["link_id"], lk))
    return out


def _download(
    db: Db,
    fetcher: Fetcher,
    robots: RobotsCache,
    company: dict[str, Any],
    candidates: list[tuple[int, Link]],
    opts: IrOptions,
    run_id: int | None,
    budget: list[int],
) -> tuple[int, int]:
    want = [(i, lk) for i, lk in candidates if lk.kind in opts.kinds]
    want.sort(key=lambda x: x[1].period.period_end if x[1].period else date.min, reverse=True)
    new_docs = failed = 0
    for link_id, lk in want:
        if budget[0] <= 0:
            break
        if not robots.allowed(lk.url):
            _link_status(db, link_id, "robots_blocked", "robots.txt disallows this URL")
            continue
        budget[0] -= 1
        try:
            r = fetcher.get(lk.url, allow_404=True, return_status=(401, 403, 410))
        except TransientError as e:
            _link_status(db, link_id, "failed", str(e)[:500])
            failed += 1
            continue
        if r is None or r.status != 200:
            _link_status(db, link_id, "skipped", f"HTTP {r.status if r else 404}")
            continue
        mime = sniff_mime(r.body)
        if mime is None:
            kind = (
                "HTML page" if r.body[:512].lstrip().lower().startswith((b"<!doctype", b"<html")) else "unknown bytes"
            )
            _link_status(db, link_id, "failed", f"not a document ({kind}); login or disclaimer page?")
            failed += 1
            continue
        doc_id = _register(db, company, link_id, lk, r.body, r.sha256, mime, run_id)
        db.execute("UPDATE stg_ir_link SET status='downloaded', status_note=NULL, doc_id=:d WHERE link_id=:l",
                   {"d": doc_id, "l": link_id})  # fmt: skip
        db.commit()
        new_docs += 1
    return new_docs, failed


def _register(
    db: Db, company: dict[str, Any], link_id: int, lk: Link, body: bytes, sha: str, mime: str, run_id: int | None
) -> int:
    period = lk.period
    title: str | None = lk.text or None
    if period is None or not title:
        pdf_title, pdf_period = pdf_title_and_period(body, company.get("fiscal_year_end"))
        period = period or pdf_period
        title = title or pdf_title
    url = f"{lk.url}#sha256={sha}"
    existing = db.scalar(
        "SELECT doc_id FROM stg_document WHERE source='ir_website' AND source_url_hash=SHA2(:u, 256)", {"u": url}
    )
    if existing:
        return int(existing)
    doc_id = db.execute(
        """INSERT INTO stg_document (company_id, doc_kind, kind_source, source, source_url, original_filename, title,
             link_id, mime_type, period_end_date, period_source, published_date, registered_run_id)
           VALUES (:c, :k, :ks, 'ir_website', :u, :fn, :ti, :l, :m, :pe, :ps, :pd, :run)""",
        {
            "c": company["company_id"],
            "k": lk.kind,
            "ks": f"link:{lk.kind_reason}"[:32],
            "u": url,
            "fn": unquote(urlparse(lk.url).path.rsplit("/", 1)[-1])[:255] or None,
            "ti": (title or "")[:512] or None,
            "l": link_id,
            "m": mime,
            "pe": period.period_end if period else None,
            "ps": period.source if period else None,
            "pd": date.today(),
            "run": run_id,
        },
    )
    mark_fetched(db, doc_id, sha, len(body))
    return doc_id


def _link_status(db: Db, link_id: int, status: str, note: str) -> None:
    db.execute("UPDATE stg_ir_link SET status=:s, status_note=:n WHERE link_id=:l",
               {"s": status, "n": note[:512], "l": link_id})  # fmt: skip
    db.commit()


# ------------------------------------------------------------------ state
def _update_source(db: Db, source_id: int, d: Discovery, supp: int) -> None:
    failed = d.outcome in FAILURE_OUTCOMES
    got_page = d.outcome in (M.OK, M.NO_DOCUMENTS, M.JS_ONLY, M.LAYOUT_BREAK) and d.http_status == 200
    db.execute(
        """UPDATE ref_ir_source SET last_checked_at=CURRENT_TIMESTAMP(6), last_outcome=:o,
             consecutive_failures=IF(:f, consecutive_failures+1, 0),
             http_etag=IF(:g, :et, http_etag), http_last_modified=IF(:g, :lm, http_last_modified),
             last_changed_at=IF(:g AND NOT (last_sha256 <=> :sha), CURRENT_TIMESTAMP(6), last_changed_at),
             last_sha256=IF(:g, :sha, last_sha256),
             last_link_count=IF(:g, :n, last_link_count),
             max_link_count=IF(:g, GREATEST(COALESCE(max_link_count, 0), :n), max_link_count)
           WHERE source_id=:s""",
        {
            "o": d.outcome,
            "f": int(failed),
            "g": int(got_page),
            "et": (d.etag or "")[:255] or None,
            "lm": (d.last_modified or "")[:64] or None,
            "sha": d.page_sha256,
            "n": supp,
            "s": source_id,
        },
    )


def _log_check(
    db: Db,
    run_id: int | None,
    cid: int,
    source_id: int | None,
    strategy: str,
    d: Discovery,
    supp: int,
    links: int = 0,
    new_links: int = 0,
    new_docs: int = 0,
) -> None:
    periods = [lk.period.period_end for lk in d.supplemental_links if lk.period]
    db.execute(
        """INSERT INTO ops_ir_check (run_id, company_id, source_id, strategy, outcome, http_status, links_found,
             supp_links, new_links, new_docs, latest_supp_period, note)
           VALUES (:r, :c, :s, :st, :o, :h, :l, :sp, :nl, :nd, :lp, :n)""",
        {
            "r": run_id,
            "c": cid,
            "s": source_id,
            "st": strategy[:16],
            "o": d.outcome,
            "h": d.http_status,
            "l": links,
            "sp": supp,
            "nl": new_links,
            "nd": new_docs,
            "lp": max(periods) if periods else None,
            "n": (d.note or "")[:1024] or None,
        },
    )
    db.commit()
