"""Ways to list a company's IR documents, behind one interface.

    strategy.discover(page) -> Discovery(outcome, links, ...)

  static_html  GET the results page (conditional on ETag / Last-Modified), parse anchors.
               Works for Notified/gcs-web "static-files", Equisolve (CloudFront), WordPress,
               Investis and most custom sites. A page that loads but carries no document
               links and looks script-built comes back js_only.
  q4_feed      Q4 Inc's public JSON feed (FinancialReport.svc). Q4 says API use needs the
               site owner's permission, so this runs only with a key configured for that
               company ([q4_feed] <ticker> = key, or REITSTREET_Q4_FEED_<TICKER>). Keys
               embedded in a site's own pages are never scraped. Key never stored.
  headless     Render the page in Chromium (Playwright) and parse the result. Opt-in
               ([ir] headless = true) and optional dependency: without it the outcome is
               needs_headless, never a crash.
  sec_8k       Not a crawl: reports the latest supplemental already registered from SEC
               8-K EX-99 exhibits (sec-sync). Used as fallback and cross-check.

The chain per company: its profile strategy first, then static_html, then headless
(when the page was js_only), and the SEC cross-check always runs for the overdue check.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode, urljoin, urlparse

from ...core.db import Db
from ...core.errors import TransientError
from ...core.http import Fetcher
from . import models as M
from .classify import classify_link, is_document_url, link_period, quarter_end
from .links import extract_links, looks_js_rendered
from .models import Discovery, Link, Period
from .robots import RobotsCache

log = logging.getLogger(__name__)


@dataclass
class Page:
    company_id: int
    ticker: str
    source_id: int | None
    url: str
    fye_mmdd: str | None
    etag: str | None = None
    last_modified: str | None = None


@dataclass
class StrategyEnv:
    db: Db | None
    fetcher: Fetcher
    robots: RobotsCache
    headless_enabled: bool = False
    q4_key: str | None = None
    conditional: bool = True


class Strategy(Protocol):
    name: str

    def discover(self, env: StrategyEnv, page: Page) -> Discovery: ...


# ------------------------------------------------------------------ static HTML
class StaticHtml:
    name = "static_html"

    def discover(self, env: StrategyEnv, page: Page) -> Discovery:
        if not env.robots.allowed(page.url):
            return Discovery(self.name, M.ROBOTS_BLOCKED, note="robots.txt disallows the results page")
        try:
            r = env.fetcher.get(
                page.url,
                allow_404=True,
                etag=page.etag if env.conditional else None,
                last_modified=page.last_modified if env.conditional else None,
                return_status=(401, 403, 410),
            )
        except TransientError as e:
            return Discovery(self.name, M.HTTP_ERROR, note=str(e)[:500])
        if r is None:
            return Discovery(self.name, M.HTTP_ERROR, 404, note="results page returned 404")
        if r.not_modified:
            return Discovery(self.name, M.NOT_MODIFIED, 304, etag=page.etag, last_modified=page.last_modified)
        if r.status != 200:
            return Discovery(self.name, M.HTTP_ERROR, r.status, note=f"HTTP {r.status}")
        html = r.text()
        links = extract_links(html, page.url, page.fye_mmdd)
        d = Discovery(
            self.name,
            M.OK,
            200,
            links,
            etag=r.headers.get("etag"),
            last_modified=r.headers.get("last-modified"),
            page_sha256=r.sha256,
        )
        if not links:
            d.outcome = M.JS_ONLY if looks_js_rendered(html) else M.NO_DOCUMENTS
        elif not d.supplemental_links and looks_js_rendered(html):
            d.note = "document links found but no supplemental; page may load the rest with JavaScript"
        return d


# ------------------------------------------------------------------ Q4 feed
Q4_REPORT_TYPES = "First Quarter|Second Quarter|Third Quarter|Fourth Quarter|Annual Report|Supplemental Report"
Q4_QUARTER = {"first quarter": 1, "second quarter": 2, "third quarter": 3, "fourth quarter": 4, "q1": 1, "q2": 2,
              "q3": 3, "q4": 4}  # fmt: skip


def q4_feed_url(page_url: str, api_key: str) -> tuple[str, str]:
    """(request URL with key, URL safe to store). The feed lives on the IR site's own host."""
    p = urlparse(page_url)
    base = f"{p.scheme or 'https'}://{p.netloc}/feed/FinancialReport.svc/GetFinancialReportList"
    q = {"LanguageId": 1, "Year": -1, "pageSize": -1, "pageNumber": 0, "includeTags": "true",
         "reportTypes": Q4_REPORT_TYPES}  # fmt: skip
    return f"{base}?{urlencode({'apiKey': api_key, **q})}", f"{base}?{urlencode({'apiKey': 'REDACTED', **q})}"


def parse_q4_feed(data: dict[str, Any], page_url: str, fye_mmdd: str | None) -> list[Link]:
    out: dict[str, Link] = {}
    for rep in data.get("GetFinancialReportListResult") or []:
        title = str(rep.get("ReportTitle") or "")
        sub = str(rep.get("ReportSubType") or "").strip().lower()
        year = rep.get("ReportYear")
        for doc in rep.get("Documents") or []:
            path = str(doc.get("DocumentPath") or "")
            if not path:
                continue
            url = urljoin(page_url, path)
            if not is_document_url(url) and str(doc.get("DocumentFileType") or "").lower() not in (
                "pdf",
                "xlsx",
                "xls",
            ):
                continue
            text = " ".join(x for x in (title, str(doc.get("DocumentTitle") or "")) if x)
            cat = str(doc.get("DocumentCategory") or doc.get("DocumentType") or "")
            kind, reason = classify_link(text, url, cat)
            if kind == "unknown" and "supplement" in sub:
                kind, reason = "supplemental", "q4_subtype"
            lk = Link(url=url, text=text[:512], context=f"{rep.get('ReportSubType') or ''} {year or ''}".strip(),
                      kind=kind, kind_reason=reason)  # fmt: skip
            q = Q4_QUARTER.get(sub)
            if q and isinstance(year, int | str) and str(year).isdigit():
                y = int(year)
                lk.period = Period(y, q, quarter_end(y, q, fye_mmdd), "feed")
            else:
                lk.period = link_period(text, url, "", fye_mmdd)
            out.setdefault(url, lk)
    return list(out.values())


class Q4Feed:
    name = "q4_feed"

    def discover(self, env: StrategyEnv, page: Page) -> Discovery:
        if not env.q4_key:
            return Discovery(self.name, M.NOT_CONFIGURED, note="no Q4 feed key configured for this company")
        url, safe = q4_feed_url(page.url, env.q4_key)
        if not env.robots.allowed(url):
            return Discovery(self.name, M.ROBOTS_BLOCKED, note="robots.txt disallows the Q4 feed")
        try:
            r = env.fetcher.get(url, allow_404=True, record_url=safe, return_status=(401, 403))
        except TransientError as e:
            return Discovery(self.name, M.HTTP_ERROR, note=str(e)[:500])
        if r is None or r.status != 200:
            return Discovery(self.name, M.HTTP_ERROR, r.status if r else 404, note="Q4 feed refused the request")
        try:
            links = parse_q4_feed(r.json(), page.url, page.fye_mmdd)
        except ValueError as e:
            return Discovery(self.name, M.HTTP_ERROR, 200, note=f"Q4 feed returned non-JSON: {e}"[:500])
        return Discovery(self.name, M.OK if links else M.NO_DOCUMENTS, 200, links, page_sha256=r.sha256)


# ------------------------------------------------------------------ headless
class Headless:
    """Playwright Chromium render. Network: one page load plus the page's own scripts;
    the host rate limit and robots.txt apply to the page URL."""

    name = "headless"
    timeout_ms = 45_000

    def discover(self, env: StrategyEnv, page: Page) -> Discovery:
        if not env.headless_enabled:
            return Discovery(self.name, M.NEEDS_HEADLESS, note="page needs a browser; set [ir] headless = true")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return Discovery(
                self.name,
                M.NEEDS_HEADLESS,
                note="Playwright not installed: pip install playwright, then python -m playwright install chromium",
            )
        if not env.robots.allowed(page.url):
            return Discovery(self.name, M.ROBOTS_BLOCKED, note="robots.txt disallows the results page")
        env.fetcher.limiter.wait(page.url)
        t0 = time.monotonic()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                try:
                    ctx = browser.new_context(user_agent=env.fetcher.user_agent)
                    pg = ctx.new_page()
                    resp = pg.goto(page.url, wait_until="networkidle", timeout=self.timeout_ms)
                    html = pg.content()
                    status = resp.status if resp else 200
                finally:
                    browser.close()
        except Exception as e:  # Playwright raises its own error types; all mean "this render failed"
            return Discovery(self.name, M.HTTP_ERROR, note=f"headless render failed: {type(e).__name__}: {e}"[:500])
        body = html.encode("utf-8")
        f = env.fetcher.capture(page.url, status, {"content-type": "text/html; rendered=headless"}, body,
                                int((time.monotonic() - t0) * 1000))  # fmt: skip
        links = extract_links(html, page.url, page.fye_mmdd)
        return Discovery(self.name, M.OK if links else M.NO_DOCUMENTS, status, links, page_sha256=f.sha256)


# ------------------------------------------------------------------ SEC cross-check
@dataclass
class SecLatest:
    period_end: Any
    published: Any
    doc_id: int | None


def sec_latest_supplemental(db: Db, company_id: int) -> SecLatest | None:
    """Latest supplemental registered from SEC 8-K exhibits (sec-sync does the downloading)."""
    row = db.one(
        """SELECT doc_id, period_end_date, published_date FROM stg_document
           WHERE company_id=:c AND source='sec_8k_exhibit' AND doc_kind='supplemental' AND status<>'failed'
           ORDER BY period_end_date DESC, published_date DESC LIMIT 1""",
        {"c": company_id},
    )
    return SecLatest(row["period_end_date"], row["published_date"], row["doc_id"]) if row else None


STRATEGIES: dict[str, Strategy] = {s.name: s for s in (StaticHtml(), Q4Feed(), Headless())}


def chain(profile_strategy: str | None, *, q4_key: str | None) -> list[str]:
    """Order in which strategies are tried for one results page."""
    first = profile_strategy or "static_html"
    if first in ("sec_8k", "none"):
        return []
    order = ["q4_feed"] if q4_key else []
    if first != "q4_feed" and first not in order:
        order.append(first)
    order += [s for s in ("static_html", "headless") if s not in order]
    return order


def run_chain(env: StrategyEnv, page: Page, order: list[str]) -> Discovery:
    """Try strategies in order and return the first definite answer. A strategy that is not
    configured, a Q4 feed error, or a script-built (js_only) page moves on to the next one.
    Headless only ever renders a page that static HTML found script-built."""
    last: Discovery | None = None
    for name in order:
        if name == "headless" and last is not None and last.outcome != M.JS_ONLY:
            break
        d = STRATEGIES[name].discover(env, page)
        if name == "headless" and d.outcome == M.NEEDS_HEADLESS and last is not None:
            last.note = d.note
            return last
        moves_on = d.outcome in (M.NOT_CONFIGURED, M.JS_ONLY, M.NEEDS_HEADLESS) or (
            name == "q4_feed" and d.outcome == M.HTTP_ERROR
        )
        if not moves_on:
            return d
        if last is None or d.outcome != M.NOT_CONFIGURED:
            last = d
    return last or Discovery("none", M.NOT_CONFIGURED, note="no strategy for this company")


def q4_key_for(ticker: str, extra: dict[str, str], environ: dict[str, str] | None = None) -> str | None:
    env = os.environ if environ is None else environ
    t = ticker.lower().replace(".", "_").replace("-", "_")
    return (env.get(f"REITSTREET_Q4_FEED_{t.upper()}") or extra.get(f"q4_feed_{t}") or "").strip() or None
