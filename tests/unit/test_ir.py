"""IR website fetcher: classification, periods, link extraction, robots.txt, strategies."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from reitstreet.core.http import Fetcher, HostRateLimiter, fixture_transport
from reitstreet.core.rawstore import RawStore
from reitstreet.sources.ir import models as M
from reitstreet.sources.ir.classify import (
    classify_link,
    infer_period,
    is_document_url,
    link_period,
    period_from_date,
    quarter_end,
)
from reitstreet.sources.ir.expectations import expect_company, next_quarter_end, typical_lag
from reitstreet.sources.ir.fetch import sniff_mime
from reitstreet.sources.ir.links import extract_links, looks_js_rendered
from reitstreet.sources.ir.profiles import read_profiles
from reitstreet.sources.ir.robots import RobotsCache, Rules, parse_robots
from reitstreet.sources.ir.strategies import (
    Page,
    StrategyEnv,
    chain,
    parse_q4_feed,
    q4_feed_url,
    q4_key_for,
    run_chain,
)

TODAY = date(2026, 11, 26)


# ---------------------------------------------------------------- classification
@pytest.mark.parametrize(
    ("text", "url", "ctx", "kind"),
    [
        ("Supplemental Information", "https://x.com/a.pdf", "", "supplemental"),
        ("Download", "https://x.com/files/Q3-2026-Supplemental.pdf", "", "supplemental"),
        ("PDF", "https://x.com/a.pdf", "Financial Package", "supplemental"),
        ("Earnings Release", "https://x.com/a.pdf", "", "earnings_release"),
        ("Investor Presentation", "https://x.com/a.pdf", "", "presentation"),
        ("2025 Sustainability Report", "https://x.com/supplemental.pdf", "", "exhibit_other"),
        ("Proxy Statement", "https://x.com/a.pdf", "", "exhibit_other"),
        ("Q3 2026", "https://x.com/a.pdf", "", "unknown"),
    ],
)
def test_classify_link(text: str, url: str, ctx: str, kind: str) -> None:
    assert classify_link(text, url, ctx)[0] == kind


def test_is_document_url() -> None:
    assert is_document_url("https://x.com/a/b.PDF")
    assert is_document_url("https://x.com/q3.xlsx")
    assert is_document_url("https://s.q4cdn.com/123/files/doc_financials/2026/q3/supp.pdf")
    assert is_document_url("https://ir.x.com/static-files/abc-123")
    assert not is_document_url("https://x.com/news/release")
    assert not is_document_url("ftp://x.com/a.pdf")


@pytest.mark.parametrize(
    ("text", "fy", "q"),
    [
        ("Q3 2026 Supplemental", 2026, 3),
        ("3Q26 Supplemental", 2026, 3),
        ("Supplemental 2026 Q2", 2026, 2),
        ("Third Quarter 2026 Results", 2026, 3),
        ("2026 Third Quarter Supplemental", 2026, 3),
        ("supp_q1-26.pdf", 2026, 1),
        ("Period ended 09.30.2026", 2026, 3),
        ("2026-06-30 package", 2026, 2),
        ("September 30, 2026", 2026, 3),
        ("Full Year 2025", 2025, 4),
    ],
)
def test_infer_period(text: str, fy: int, q: int) -> None:
    p = infer_period(text, "1231", today=TODAY)
    assert p is not None and (p.fiscal_year, p.fiscal_quarter) == (fy, q)
    assert p.period_end == quarter_end(fy, q, "1231")


def test_infer_period_quarter_token_beats_publish_date() -> None:
    p = infer_period("Q3 2026 Supplemental posted 10/28/2026", "1231", today=TODAY)
    assert p is not None and p.fiscal_quarter == 3


def test_infer_period_rejects_non_quarter_dates_and_nonsense() -> None:
    assert infer_period("posted October 28, 2026", "1231", today=TODAY) is None
    assert infer_period("Supplemental", "1231", today=TODAY) is None


def test_fiscal_year_not_december() -> None:
    # fiscal year ends June 30: FY2026 Q1 ends Sep 30, 2025
    assert quarter_end(2026, 1, "0630") == date(2025, 9, 30)
    assert quarter_end(2026, 4, "0630") == date(2026, 6, 30)
    p = period_from_date(date(2025, 9, 30), "0630")
    assert p is not None and (p.fiscal_year, p.fiscal_quarter) == (2026, 1)
    assert period_from_date(date(2026, 8, 31), "1231") is None


def test_link_period_records_its_source() -> None:
    p = link_period("Download", "https://x.com/2026/q3/supp.pdf", "", "1231", today=TODAY)
    assert p is not None and p.source == "url" and p.fiscal_quarter == 3
    p = link_period("PDF", "https://x.com/doc.pdf", "Second Quarter 2026", "1231", today=TODAY)
    assert p is not None and p.source == "context" and p.fiscal_quarter == 2
    p = link_period("Q1 2026 Supplemental", "https://x.com/2025/q4/a.pdf", "", "1231", today=TODAY)
    assert p is not None and p.source == "anchor" and p.fiscal_quarter == 1


# ---------------------------------------------------------------- links
PAGE = """<html><head><base href="https://ir.x.com/files/"></head><body>
<script>var a = '<a href="evil.pdf">Supplemental</a>';</script>
<h3>Third Quarter 2026</h3>
<table><tr><td>Supplemental</td><td><a href="q3-supp.pdf"><img src="pdf.png"></a></td></tr>
<tr><td><a href="q3-supp.pdf">Supplemental Package</a></td></tr>
<tr><td><a href="q3-release.pdf">Earnings Release</a></td></tr></table>
<a href="mailto:ir@x.com">Contact</a><a href="/news">News</a>
<a href="https://ir.x.com/files/2026/q2/supplemental.pdf#page=2">Q2 2026 Supplemental</a>
</body></html>"""


def test_extract_links() -> None:
    links = {lk.url: lk for lk in extract_links(PAGE, "https://ir.x.com/results/")}
    assert set(links) == {
        "https://ir.x.com/files/q3-supp.pdf",
        "https://ir.x.com/files/q3-release.pdf",
        "https://ir.x.com/files/2026/q2/supplemental.pdf",
    }
    supp = links["https://ir.x.com/files/q3-supp.pdf"]
    assert supp.kind == "supplemental" and supp.period is not None
    assert (supp.period.fiscal_year, supp.period.fiscal_quarter, supp.period.source) == (2026, 3, "context")
    assert links["https://ir.x.com/files/q3-release.pdf"].kind == "earnings_release"
    q2 = links["https://ir.x.com/files/2026/q2/supplemental.pdf"]
    assert q2.period is not None and q2.period.fiscal_quarter == 2 and q2.period.source == "anchor"


def test_looks_js_rendered() -> None:
    assert looks_js_rendered('<div class="module_container"></div><script src="https://s.q4cdn.com/q4.js">')
    assert not looks_js_rendered("<html><body><p>Hello</p></body></html>")


# ---------------------------------------------------------------- robots.txt
ROBOTS = """
User-agent: *
Disallow: /private/
Crawl-delay: 2

User-agent: ReitStreetBot
Disallow: /files/
Allow: /files/public/
Disallow: /*.xlsx$

User-agent: OtherBot
Disallow: /
"""


def test_robots_specific_group_longest_match_wins() -> None:
    r = parse_robots(ROBOTS, "ReitStreetBot")
    assert not r.allowed("https://x.com/files/a.pdf")
    assert r.allowed("https://x.com/files/public/a.pdf")
    assert r.allowed("https://x.com/files/public/a.xlsx")  # /files/public/ (14) is longer than /*.xlsx$ (8)
    assert not r.allowed("https://x.com/data/a.xlsx")
    assert r.allowed("https://x.com/private/a.pdf")  # the * group does not apply when a specific one matches
    assert r.allowed("https://x.com/robots.txt")
    assert r.crawl_delay is None


def test_robots_star_group_and_crawl_delay() -> None:
    r = parse_robots(ROBOTS, "SomeoneElse")
    assert not r.allowed("https://x.com/private/a.pdf")
    assert r.allowed("https://x.com/files/a.pdf")
    assert r.crawl_delay == 2.0


def test_robots_allow_wins_ties_and_empty_disallow() -> None:
    r = parse_robots("User-agent: *\nDisallow: /a\nAllow: /a\n", "ReitStreetBot")
    assert r.allowed("https://x.com/a/b")
    assert parse_robots("User-agent: *\nDisallow:\n", "ReitStreetBot").allowed("https://x.com/anything")
    assert Rules("disallow_all").allowed("https://x.com/a") is False
    assert Rules("allow_all").allowed("https://x.com/a") is True


def _web_fetcher(tmp_path: Path, transport: Any, limiter: HostRateLimiter | None = None) -> Fetcher:
    f = Fetcher(
        source="ir_website",
        user_agent="ReitStreetBot/0.3.0 (test)",
        rps=1000,
        store=RawStore.local(tmp_path / "raw"),
        db=None,
        transport=transport,
        max_retries=1,
        limiter=limiter,
    )
    f._sleep = lambda s: None
    return f


def test_robots_cache_policies(tmp_path: Path) -> None:
    answers = {
        "a.com": (200, {}, b"User-agent: *\nDisallow: /x\nCrawl-delay: 99\n"),
        "b.com": (404, {}, b""),
        "c.com": (403, {}, b""),
        "d.com": (503, {}, b""),
    }
    calls: list[str] = []

    def transport(url: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
        calls.append(url)
        return answers[url.split("/")[2]]

    lim = HostRateLimiter(1000, sleep=lambda s: None)
    rc = RobotsCache(_web_fetcher(tmp_path, transport, lim), None, agent_token="ReitStreetBot")
    assert not rc.allowed("https://a.com/x/1.pdf") and rc.allowed("https://a.com/y.pdf")
    assert lim.interval("a.com") == 30.0  # Crawl-delay honored, capped at 30 s
    assert rc.allowed("https://b.com/x")  # 404: no robots.txt, allow all
    assert rc.allowed("https://c.com/x")  # 4xx: allow all (RFC 9309)
    assert not rc.allowed("https://d.com/x")  # 5xx: unreachable, disallow all
    n = len(calls)
    rc.allowed("https://a.com/z")
    assert len(calls) == n  # cached per host


# ---------------------------------------------------------------- HTTP
def test_host_rate_limiter_is_per_host() -> None:
    slept: list[float] = []
    lim = HostRateLimiter(1.0, sleep=slept.append)
    lim.wait("https://a.com/1")
    lim.wait("https://b.com/1")  # another host: no wait
    assert slept == []
    lim.wait("https://a.com/2")
    assert len(slept) == 1 and 0.9 < slept[0] <= 1.0
    lim.set_min_interval("b.com", 5)
    assert lim.interval("b.com") == 5 and lim.interval("c.com") == 1.0


def test_conditional_get_with_fixture_transport(tmp_path: Path) -> None:
    site = tmp_path / "site" / "ir.x.com" / "results"
    site.mkdir(parents=True)
    (site / "index.html").write_text("<a href='a.pdf'>Supplemental</a>")
    f = _web_fetcher(tmp_path, fixture_transport(tmp_path / "site"))
    r = f.get("https://ir.x.com/results/")
    assert r is not None and r.status == 200 and r.headers["etag"]
    r2 = f.get("https://ir.x.com/results/", etag=r.headers["etag"])
    assert r2 is not None and r2.not_modified and r2.body == b""
    (site / "index.html").write_text("<a href='b.pdf'>Supplemental</a>")
    r3 = f.get("https://ir.x.com/results/", etag=r.headers["etag"])
    assert r3 is not None and r3.status == 200 and b"b.pdf" in r3.body


def test_sniff_mime() -> None:
    assert sniff_mime(b"%PDF-1.7\n") == "application/pdf"
    assert sniff_mime(b"PK\x03\x04rest").endswith("spreadsheetml.sheet")  # type: ignore[union-attr]
    assert sniff_mime(b"<!DOCTYPE html>") is None


# ---------------------------------------------------------------- Q4 feed
FEED = {
    "GetFinancialReportListResult": [
        {
            "ReportTitle": "Third Quarter 2026",
            "ReportSubType": "Third Quarter",
            "ReportYear": 2026,
            "Documents": [
                {
                    "DocumentPath": "https://s.q4cdn.com/1/files/doc_financials/2026/q3/Supp.pdf",
                    "DocumentTitle": "Supplemental",
                    "DocumentFileType": "PDF",
                },
                {"DocumentPath": "/files/doc_financials/2026/q3/release.pdf", "DocumentTitle": "Earnings Release"},
                {"DocumentPath": ""},
            ],
        },
        {
            "ReportTitle": "Supplemental Report",
            "ReportSubType": "Supplemental Report",
            "ReportYear": 2026,
            "Documents": [{"DocumentPath": "https://s.q4cdn.com/1/files/x.pdf", "DocumentTitle": "Download"}],
        },
    ]
}


def test_parse_q4_feed() -> None:
    links = {lk.url: lk for lk in parse_q4_feed(FEED, "https://ir.x.com/financials/", "1231")}
    supp = links["https://s.q4cdn.com/1/files/doc_financials/2026/q3/Supp.pdf"]
    assert supp.kind == "supplemental" and supp.period is not None
    assert (supp.period.fiscal_quarter, supp.period.source) == (3, "feed")
    assert links["https://ir.x.com/files/doc_financials/2026/q3/release.pdf"].kind == "earnings_release"
    assert links["https://s.q4cdn.com/1/files/x.pdf"].kind == "supplemental"  # from the report subtype
    assert len(links) == 3


def test_q4_feed_url_redacts_key() -> None:
    real, safe = q4_feed_url("https://ir.x.com/financials/default.aspx", "SECRET123")
    assert real.startswith("https://ir.x.com/feed/FinancialReport.svc/GetFinancialReportList?")
    assert "SECRET123" in real and "SECRET123" not in safe and "REDACTED" in safe


def test_q4_feed_key_never_reaches_raw_fetch_url(tmp_path: Path) -> None:
    seen: list[str] = []

    def transport(url: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
        seen.append(url)
        if url.endswith("/robots.txt"):
            return 404, {}, b""
        return 500, {}, b""

    f = _web_fetcher(tmp_path, transport)
    env = StrategyEnv(None, f, RobotsCache(f, None, agent_token="ReitStreetBot"), q4_key="SECRET123")
    d = run_chain(env, Page(1, "X", 1, "https://ir.x.com/fin/", "1231"), ["q4_feed"])
    assert d.outcome == M.HTTP_ERROR and "SECRET123" not in d.note
    assert any("SECRET123" in u for u in seen)


def test_q4_key_lookup() -> None:
    assert q4_key_for("BRK.B", {"q4_feed_brk_b": "k1"}, environ={}) == "k1"
    assert q4_key_for("PLD", {"q4_feed_pld": "k1"}, environ={"REITSTREET_Q4_FEED_PLD": "k2"}) == "k2"
    assert q4_key_for("PLD", {}, environ={}) is None


# ---------------------------------------------------------------- strategy chain
def test_chain_order() -> None:
    assert chain("static_html", q4_key=None) == ["static_html", "headless"]
    assert chain("headless", q4_key=None) == ["headless", "static_html"]
    assert chain("static_html", q4_key="k") == ["q4_feed", "static_html", "headless"]
    assert chain("q4_feed", q4_key=None) == ["static_html", "headless"]
    assert chain("sec_8k", q4_key="k") == []
    assert chain(None, q4_key=None) == ["static_html", "headless"]


def _site(tmp_path: Path, html: str) -> Path:
    d = tmp_path / "site" / "ir.x.com" / "results"
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text(html)
    return tmp_path / "site"


def test_run_chain_static_then_headless_disabled(tmp_path: Path) -> None:
    html = '<div id="q4-module"><script src="https://s.q4cdn.com/w.js"></script></div>'
    f = _web_fetcher(tmp_path, fixture_transport(_site(tmp_path, html)))
    env = StrategyEnv(None, f, RobotsCache(f, None, agent_token="ReitStreetBot"), headless_enabled=False)
    d = run_chain(env, Page(1, "X", 1, "https://ir.x.com/results/", "1231"), ["static_html", "headless"])
    assert d.strategy == "static_html" and d.outcome == M.JS_ONLY
    assert "headless" in d.note


def test_run_chain_static_finds_links_and_skips_headless(tmp_path: Path) -> None:
    f = _web_fetcher(tmp_path, fixture_transport(_site(tmp_path, PAGE)))
    env = StrategyEnv(None, f, RobotsCache(f, None, agent_token="ReitStreetBot"), headless_enabled=True)
    d = run_chain(env, Page(1, "X", 1, "https://ir.x.com/results/", "1231"), ["static_html", "headless"])
    assert d.strategy == "static_html" and d.outcome == M.OK and len(d.supplemental_links) == 2


def test_run_chain_robots_blocked(tmp_path: Path) -> None:
    root = _site(tmp_path, PAGE)
    (root / "ir.x.com" / "robots.txt").write_text("User-agent: *\nDisallow: /results/\n")
    f = _web_fetcher(tmp_path, fixture_transport(root))
    env = StrategyEnv(None, f, RobotsCache(f, None, agent_token="ReitStreetBot"))
    d = run_chain(env, Page(1, "X", 1, "https://ir.x.com/results/", "1231"), ["static_html", "headless"])
    assert d.outcome == M.ROBOTS_BLOCKED


# ---------------------------------------------------------------- profiles
def test_bundled_profiles_are_valid() -> None:
    rows = read_profiles()
    assert len(rows) == len({r["ticker"] for r in rows}) >= 150
    assert {r["strategy"] for r in rows} <= {"static_html", "q4_feed", "headless", "sec_8k", "none"}


def test_profiles_reject_unknown_strategy() -> None:
    head = "ticker,strategy,platform,expects_supplemental,probe_status,probe_supp_links,probe_latest_period"
    text = head + ",probe_date,notes\nX,magic,,1,works,,,,\n"
    with pytest.raises(ValueError, match="unknown strategy"):
        read_profiles(text)


# ---------------------------------------------------------------- expectations
def _supp(pe: str, pub: str) -> dict[str, Any]:
    return {"period_end_date": date.fromisoformat(pe), "published_date": date.fromisoformat(pub)}


def test_typical_lag_and_next_quarter() -> None:
    assert typical_lag([]) == 45
    assert typical_lag([20, 22, 25]) == 30  # clamped up
    assert typical_lag([40, 42, 500]) == 41  # backfill lag ignored
    assert next_quarter_end(date(2026, 9, 30)) == date(2026, 12, 31)
    assert next_quarter_end(date(2026, 12, 31)) == date(2027, 3, 31)


def test_expect_company_ok_overdue_and_release_signal() -> None:
    c = {"company_id": 1, "ticker": "X", "fiscal_year_end": "1231"}
    supp = [_supp("2026-03-31", "2026-04-28"), _supp("2026-06-30", "2026-07-28")]
    e = expect_company(c, supp, [], date(2026, 9, 15))
    assert e.status == "ok" and e.next_period == date(2026, 9, 30) and e.lag_days == 30
    assert e.due_date == date(2026, 11, 6)  # 30 day lag + 7 grace
    assert expect_company(c, supp, [], date(2026, 11, 7)).status == "overdue"
    rel = [{"period_end_date": date(2026, 9, 30), "published_date": date(2026, 10, 20)}]
    e = expect_company(c, supp, rel, date(2026, 10, 24))
    assert e.status == "overdue" and "earnings release" in e.reason
    assert expect_company(c, [], [], TODAY).status == "no_baseline"


def test_expect_company_q4_gets_extra_time() -> None:
    c = {"company_id": 1, "ticker": "X", "fiscal_year_end": "1231"}
    supp = [_supp("2026-06-30", "2026-08-10"), _supp("2026-09-30", "2026-11-10")]
    e = expect_company(c, supp, [], TODAY)
    assert e.next_period == date(2026, 12, 31)
    assert e.lag_days == 41 and e.due_date == date(2027, 3, 4)  # 41 day lag + 15 for fiscal Q4 + 7 grace
