"""End-to-end: migrate, universe, SEC sync on offline fixtures, warehouse, idempotency."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from reitstreet.core.db import Db
from tests.integration.helpers import bootstrap, cli, last_run, metric
from tests.sec_fixtures import REITS, quarters_2024

pytestmark = pytest.mark.mysql


def test_full_offline_pipeline(env: Path, sec_root: Path, db: Db) -> None:
    bootstrap(sec_root)
    run = last_run(db, "sec-sync")
    assert run["status"] == "succeeded"
    assert run["items_ok"] == 12
    assert db.scalar("SELECT COUNT(*) FROM ref_company WHERE in_universe=1") == 12
    # 10 filings per company plus PLD's paged older 10-K
    assert db.scalar("SELECT COUNT(*) FROM stg_sec_filing") == 12 * 10 + 1
    # 3 earnings 8-Ks x 2 exhibits each (EX-99.1 release, EX-99.2 supplemental), downloaded
    assert db.scalar("SELECT COUNT(*) FROM stg_document WHERE source='sec_8k_exhibit'") == 12 * 3 * 2
    assert db.scalar("SELECT COUNT(*) FROM stg_document WHERE status='fetched'") == 72
    assert db.scalar("SELECT COUNT(*) FROM stg_document WHERE doc_kind='supplemental'") == 36
    # every request is captured; the only failure is PLD's 2023 index page, absent from the fixture (404)
    assert db.scalar("SELECT COUNT(*) FROM raw_fetch WHERE sha256 IS NULL AND http_status <> 404") == 0
    assert db.scalar("SELECT COUNT(*) FROM raw_fetch WHERE http_status = 404") == 1
    assert db.scalar("SELECT COUNT(*) FROM raw_object") > 100
    for t in REITS:
        q = quarters_2024(t)
        fy = metric(db, t, "TOTAL_REVENUE", "FY", "2024-12-31")
        assert [r["value"] for r in fy] == [Decimal(sum(q))], t
        q4 = metric(db, t, "TOTAL_REVENUE", "Q", "2024-12-31", source="derived")
        assert [r["value"] for r in q4] == [Decimal(q[3])], t
        assert "minus" in q4[0]["derivation"]


def test_restatement_versions(env: Path, sec_root: Path, db: Db) -> None:
    bootstrap(sec_root)
    q1 = quarters_2024("PLD")[0]
    rows = metric(db, "PLD", "TOTAL_REVENUE", "Q", "2024-03-31")
    assert [(r["version"], r["value"]) for r in rows] == [(1, Decimal(q1)), (2, Decimal(int(q1 * 1.01)))]
    assert str(rows[0]["known_from"]) == "2024-04-30"
    assert str(rows[0]["known_to"]) == "2025-04-30"
    assert rows[1]["known_to"] is None
    # a re-reported identical value is not a new version
    assert len(metric(db, "SPG", "TOTAL_REVENUE", "Q", "2024-03-31")) == 1
    # derived Q2 = H1 - Q1 is not created where Q2 is reported directly
    assert metric(db, "PLD", "TOTAL_REVENUE", "Q", "2024-06-30", source="derived") == []


def test_tag_priority_and_fallback(env: Path, sec_root: Path, db: Db) -> None:
    bootstrap(sec_root)
    eqr = metric(db, "EQR", "TOTAL_REVENUE", "FY", "2024-12-31")
    assert [r["value"] for r in eqr] == [Decimal(sum(quarters_2024("EQR")))]
    o = metric(db, "O", "TOTAL_REVENUE", "FY", "2024-12-31")
    assert [r["value"] for r in o] == [Decimal(sum(quarters_2024("O")))]  # not the alternate tag's 123


def test_rebuild_is_idempotent(env: Path, sec_root: Path, db: Db) -> None:
    bootstrap(sec_root)
    before = db.scalar("SELECT COUNT(*) FROM wh_metric_value")
    assert cli("sec-sync", "--offline-fixtures", str(sec_root), "--force") == 0
    assert cli("build-metrics") == 0
    assert db.scalar("SELECT COUNT(*) FROM wh_metric_value") == before
    assert db.scalar("SELECT COUNT(*) FROM wh_metric_value WHERE retired_at IS NOT NULL") == 0
    assert db.scalar("SELECT COUNT(*) FROM stg_sec_filing") == 121
    # unchanged sources: second plain sync skips submissions and facts
    assert cli("sec-sync", "--offline-fixtures", str(sec_root)) == 0
    assert db.scalar("SELECT COUNT(*) FROM wh_metric_value") == before


def test_mapping_change_retires_rows_with_history(env: Path, sec_root: Path, db: Db) -> None:
    bootstrap(sec_root)
    live = db.scalar("SELECT COUNT(*) FROM wh_metric_value WHERE is_live=1")
    db.execute("UPDATE ref_metric_map SET is_active=0 WHERE map_key='xbrl:TOTAL_REVENUE:us-gaap:Revenues:USD'")
    db.commit()
    assert cli("build-metrics", "--tickers", "PLD") == 0
    assert metric(db, "PLD", "TOTAL_REVENUE", "FY", "2024-12-31") == []
    retired = db.scalar("SELECT COUNT(*) FROM wh_metric_value WHERE retired_at IS NOT NULL")
    assert retired > 0
    db.execute("UPDATE ref_metric_map SET is_active=1 WHERE map_key='xbrl:TOTAL_REVENUE:us-gaap:Revenues:USD'")
    db.commit()
    assert cli("build-metrics", "--tickers", "PLD") == 0
    assert db.scalar("SELECT COUNT(*) FROM wh_metric_value WHERE is_live=1") == live
    assert db.scalar("SELECT COUNT(*) FROM wh_metric_value WHERE retired_at IS NOT NULL") == retired


def test_seed_is_idempotent_and_keeps_manual_rules(env: Path, db: Db) -> None:
    assert cli("migrate") == 0
    n = db.scalar("SELECT COUNT(*) FROM ref_metric_map")
    db.execute(
        """INSERT INTO ref_metric_map (map_key, metric_id, source_type, label_regex, priority, multiplier, origin)
           SELECT 'manual:FFO:test', metric_id, 'supplemental', '^ffo x$', 5, 1, 'manual' FROM ref_metric
           WHERE metric_code='FFO_NAREIT'"""
    )
    db.commit()
    assert cli("seed") == 0
    assert db.scalar("SELECT COUNT(*) FROM ref_metric_map") == n + 1
    assert db.scalar("SELECT is_active FROM ref_metric_map WHERE map_key='manual:FFO:test'") == 1
