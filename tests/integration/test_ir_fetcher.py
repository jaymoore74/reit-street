"""IR website fetcher end to end against a fixture IR site (no network)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from reitstreet.core.db import Db
from tests.integration.helpers import cli
from tests.sec_fixtures import IR_HOST

pytestmark = pytest.mark.mysql


def _page(site: Path, ticker: str, rows: list[tuple[str, str]]) -> None:
    """Results page for `ticker` listing (link text, file name) rows; each file is a small PDF."""
    d = site / IR_HOST / ticker.lower() / "results"
    d.mkdir(parents=True, exist_ok=True)
    body = "".join(f'<tr><td><a href="files/{f}">{t}</a></td></tr>' for t, f in rows)
    (d / "index.html").write_text(f"<html><body><h2>Quarterly results</h2><table>{body}</table></body></html>")
    for _t, f in rows:
        (d / "files").mkdir(exist_ok=True)
        (d / "files" / f).write_bytes(b"%PDF-1.4 " + f.encode())


PLD_ROWS = [
    ("Q2 2025 Supplemental", "q2-2025-supp.pdf"),
    ("Q1 2025 Supplemental", "q1-2025-supp.pdf"),
    ("Q4 2024 Supplemental", "q4-2024-supp.pdf"),
    ("Q2 2025 Earnings Release", "q2-2025-release.pdf"),
    ("2025 Proxy Statement", "proxy-2025.pdf"),
]


def _site(sec_root: Path, tmp_path: Path) -> Path:
    site = tmp_path / "site"
    shutil.copytree(sec_root, site)
    _page(site, "PLD", PLD_ROWS)
    return site


def _checks(db: Db, ticker: str) -> list[str]:
    return [
        r["outcome"]
        for r in db.query(
            """SELECT k.outcome FROM ops_ir_check k JOIN ref_company c USING (company_id)
               WHERE c.ticker=:t AND k.source_id IS NOT NULL ORDER BY k.check_id""",
            {"t": ticker},
        )
    ]


def _docs(db: Db, ticker: str) -> list[dict[str, object]]:
    return [
        dict(r)
        for r in db.query(
            """SELECT d.doc_kind, d.period_end_date, d.kind_source, d.period_source, d.link_id FROM stg_document d
               JOIN ref_company c USING (company_id) WHERE c.ticker=:t AND d.source='ir_website'
               ORDER BY d.doc_id""",
            {"t": ticker},
        )
    ]


def test_fetch_ir_discovers_downloads_and_reruns_quietly(env: Path, sec_root: Path, db: Db, tmp_path: Path) -> None:
    site = _site(sec_root, tmp_path)
    assert cli("migrate") == 0
    assert cli("universe") == 0
    assert db.scalar("SELECT strategy FROM ref_ir_profile JOIN ref_company USING (company_id) WHERE ticker='PLD'")
    assert cli("fetch-ir", "--offline-fixtures", str(site), "--tickers", "PLD") == 0

    links = db.query("SELECT doc_kind, status, fiscal_year, fiscal_quarter FROM stg_ir_link ORDER BY url")
    assert len(links) == 5
    kinds = sorted(r["doc_kind"] for r in links)
    assert kinds == ["earnings_release", "exhibit_other", "supplemental", "supplemental", "supplemental"]
    # default kinds = supplemental: the release and proxy are recorded but not downloaded
    assert sorted(r["status"] for r in links) == ["downloaded"] * 3 + ["new"] * 2
    docs = _docs(db, "PLD")
    from_page = [d for d in docs if d["link_id"]]
    assert len(from_page) == 3 and {str(d["period_end_date"]) for d in from_page} == {
        "2024-12-31", "2025-03-31", "2025-06-30"
    }  # fmt: skip
    assert all(d["kind_source"] and d["period_source"] == "anchor" for d in from_page)
    assert _checks(db, "PLD") == ["ok"]

    n_docs = db.scalar("SELECT COUNT(*) FROM stg_document WHERE source='ir_website'")
    assert cli("fetch-ir", "--offline-fixtures", str(site), "--tickers", "PLD") == 0
    assert db.scalar("SELECT COUNT(*) FROM stg_document WHERE source='ir_website'") == n_docs
    assert _checks(db, "PLD") == ["ok", "not_modified"]  # ETag matched: page not re-downloaded
    assert db.scalar("SELECT coverage FROM ops_ir_coverage WHERE ticker='PLD'") == "works"

    # a new quarter appears on the page: only that file is downloaded
    _page(site, "PLD", [("Q3 2025 Supplemental", "q3-2025-supp.pdf"), *PLD_ROWS])
    assert cli("fetch-ir", "--offline-fixtures", str(site), "--tickers", "PLD") == 0
    assert db.scalar("SELECT COUNT(*) FROM stg_document WHERE source='ir_website'") == n_docs + 1
    assert db.scalar("SELECT max_link_count FROM ref_ir_source WHERE source_type='results_page' "
                     "AND url LIKE '%/pld/results/'") == 4  # fmt: skip

    # --kinds pulls the earnings release on request
    assert cli("fetch-ir", "--offline-fixtures", str(site), "--tickers", "PLD", "--kinds",
               "supplemental,earnings_release", "--force") == 0  # fmt: skip
    sql = "SELECT COUNT(*) FROM stg_document WHERE source='ir_website' AND doc_kind='earnings_release'"
    assert db.scalar(sql) == 1


def test_download_cap_then_backlog(env: Path, sec_root: Path, db: Db, tmp_path: Path,
                                   monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    site = _site(sec_root, tmp_path)
    monkeypatch.setenv("REITSTREET_IR_MAX_NEW_DOCS_PER_COMPANY", "1")
    assert cli("migrate") == 0
    assert cli("universe") == 0
    assert cli("fetch-ir", "--offline-fixtures", str(site), "--tickers", "PLD") == 0
    newest = db.query("SELECT period_end_date FROM stg_document WHERE source='ir_website' AND link_id IS NOT NULL")
    assert [str(r["period_end_date"]) for r in newest] == ["2025-06-30"]  # newest first
    assert cli("fetch-ir", "--offline-fixtures", str(site), "--tickers", "PLD") == 0
    assert _checks(db, "PLD")[-1] == "not_modified"
    assert db.scalar("SELECT COUNT(*) FROM stg_document WHERE source='ir_website' AND link_id IS NOT NULL") == 2
    assert cli("fetch-ir", "--offline-fixtures", str(site), "--tickers", "PLD", "--backfill") == 0
    assert db.scalar("SELECT COUNT(*) FROM stg_document WHERE source='ir_website' AND link_id IS NOT NULL") == 3


def test_layout_break_alerts_and_dq(env: Path, sec_root: Path, db: Db, tmp_path: Path) -> None:
    site = _site(sec_root, tmp_path)
    assert cli("migrate") == 0
    assert cli("universe") == 0
    assert cli("fetch-ir", "--offline-fixtures", str(site), "--tickers", "PLD") == 0
    page = site / IR_HOST / "pld" / "results" / "index.html"
    page.write_text('<html><body><div class="redesign">Coming soon</div></body></html>')
    assert cli("fetch-ir", "--offline-fixtures", str(site), "--tickers", "PLD") == 0
    assert _checks(db, "PLD")[-1] == "layout_break"
    assert cli("monitor") == 0
    keys = {r["dedupe_key"] for r in db.query("SELECT dedupe_key FROM ops_alert")}
    assert "ir-layout:PLD" in keys
    assert cli("dq", "--only", "ops.ir_site_health") == 0
    run = db.scalar("SELECT MAX(run_id) FROM ops_dq_result")
    assert (
        db.scalar(
            "SELECT status FROM ops_dq_result WHERE run_id=:r AND check_code='ops.ir_site_health' "
            "AND subject='summary'",
            {"r": run},
        )
        == "fail"
    )


def test_overdue_supplemental_and_coverage_report(env: Path, sec_root: Path, db: Db, tmp_path: Path,
                                                  capsys: pytest.CaptureFixture[str]) -> None:  # fmt: skip
    site = _site(sec_root, tmp_path)
    assert cli("migrate") == 0
    assert cli("universe") == 0
    assert cli("fetch-ir", "--offline-fixtures", str(site), "--tickers", "PLD") == 0
    # latest held is Q2 2025, so Q3 2025 was due long ago
    assert cli("dq", "--only", "freshness.supplemental_overdue") == 0
    run = db.scalar("SELECT MAX(run_id) FROM ops_dq_result")
    fails = db.query(
        "SELECT subject FROM ops_dq_result WHERE run_id=:r AND status='fail' AND subject<>'summary'", {"r": run}
    )
    subjects = [r["subject"] for r in fails]
    assert "PLD supplemental" in subjects
    capsys.readouterr()
    out = tmp_path / "coverage.csv"
    assert cli("ir-coverage", "--out", str(out)) == 0
    printed = capsys.readouterr().out
    summary = json.loads(next(line for line in printed.splitlines() if line.startswith("{")))
    assert summary["companies"] == 12
    assert "OVERDUE PLD" in printed
    assert out.read_text().splitlines()[0].startswith("ticker,")


def test_robots_disallow_is_respected(env: Path, sec_root: Path, db: Db, tmp_path: Path) -> None:
    site = _site(sec_root, tmp_path)
    (site / IR_HOST / "robots.txt").write_text("User-agent: *\nDisallow: /pld/\n")
    assert cli("migrate") == 0
    assert cli("universe") == 0
    assert cli("fetch-ir", "--offline-fixtures", str(site), "--tickers", "PLD") == 0
    assert _checks(db, "PLD") == ["robots_blocked"]
    assert db.scalar("SELECT COUNT(*) FROM stg_document WHERE source='ir_website'") == 0
    assert db.scalar("SELECT robots_policy FROM ref_web_host WHERE host=:h", {"h": IR_HOST}) == "parsed"
