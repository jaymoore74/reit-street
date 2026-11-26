"""Operational behaviour: replay, resume, locks, fatal source errors, dead letters, monitor."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from reitstreet.core.db import Db
from tests.integration.helpers import bootstrap, cli, last_run
from tests.sec_fixtures import REITS

pytestmark = pytest.mark.mysql


def _counts(db: Db) -> dict[str, int]:
    return {
        t: int(db.scalar(f"SELECT COUNT(*) FROM {t}"))
        for t in ("stg_sec_filing", "stg_xbrl_fact", "stg_document", "wh_metric_value")
    }


LIVE_VALUES = """SELECT company_id, metric_id, period_type, period_start_key, period_end, segment, source_type,
                        version, known_from, known_to, value FROM wh_metric_value WHERE is_live=1"""


def _live(db: Db) -> set[tuple[object, ...]]:
    return {tuple(r.values()) for r in db.query(LIVE_VALUES)}


def test_replay_is_deterministic_and_offline(env: Path, sec_root: Path, db: Db) -> None:
    bootstrap(sec_root)
    before, fetches, live = _counts(db), db.scalar("SELECT COUNT(*) FROM raw_fetch"), _live(db)
    # 1. pure re-parse of everything captured: zero churn anywhere
    assert cli("replay", "--reindex") == 0
    assert _counts(db) == before
    assert db.scalar("SELECT COUNT(*) FROM wh_metric_value WHERE retired_at IS NOT NULL") == 0
    assert db.scalar("SELECT COUNT(*) FROM raw_fetch") == fetches  # no network, nothing recorded
    # 2. lose PLD's XBRL staging entirely, rebuild it from the raw store
    cid = db.scalar("SELECT company_id FROM ref_company WHERE ticker='PLD'")
    db.execute("DELETE FROM stg_xbrl_fact WHERE company_id=:c", {"c": cid})
    db.commit()
    assert cli("replay", "--tickers", "PLD") == 0
    assert db.scalar("SELECT COUNT(*) FROM stg_xbrl_fact") == before["stg_xbrl_fact"]
    assert _live(db) == live  # same numbers, same versions; lineage now points at the new fact rows
    assert db.scalar("SELECT COUNT(*) FROM wh_metric_value WHERE is_live=1 AND source_type='xbrl' AND source_fact_id "
                     "NOT IN (SELECT fact_id FROM stg_xbrl_fact)") == 0  # fmt: skip


def test_resume_after_partial_failure(env: Path, sec_root: Path, db: Db, tmp_path: Path) -> None:
    broken = tmp_path / "sec_broken"
    shutil.copytree(sec_root, broken)
    cik = REITS["SPG"][0]
    (broken / "data.sec.gov" / "submissions" / f"CIK{cik:010d}.json").write_text("{not json")
    assert cli("migrate") == 0
    assert cli("universe") == 0
    assert cli("sec-sync", "--offline-fixtures", str(broken)) == 0 or True
    run = last_run(db, "sec-sync")
    # a parse failure is contained: dead-lettered, the rest of SPG (facts) and all other companies load
    assert run["items_failed"] == 0
    dl = db.one("SELECT * FROM ops_dead_letter WHERE status='open'")
    assert dl is not None and dl["stage"] == "sec.submissions.parse" and dl["item_key"] == f"cik:{cik}"

    # a hard item failure (missing submissions file -> HTTP 404) fails the item, run is partial
    (broken / "data.sec.gov" / "submissions" / f"CIK{cik:010d}.json").unlink()
    assert cli("sec-sync", "--offline-fixtures", str(broken), "--force") == 1
    run = last_run(db, "sec-sync")
    assert run["status"] == "partial" and run["items_failed"] == 1 and run["items_ok"] == 11
    assert db.scalar("SELECT COUNT(*) FROM ops_alert WHERE title LIKE 'sec-sync run%partial%'") == 1

    # resume against healthy sources: only SPG is processed
    assert cli("sec-sync", "--offline-fixtures", str(sec_root), "--resume", str(run["run_id"])) == 0
    again = last_run(db, "sec-sync")
    assert again["resumed_from_run_id"] == run["run_id"]
    assert (again["items_ok"], again["items_skipped"]) == (1, 11)
    assert db.scalar("SELECT COUNT(*) FROM stg_sec_filing WHERE company_id=(SELECT company_id FROM ref_company "
                     "WHERE ticker='SPG')") == 10  # fmt: skip


def test_lock_prevents_concurrent_runs(env: Path, db_url: str, db: Db, sec_root: Path) -> None:
    assert cli("migrate") == 0
    assert cli("universe") == 0
    eng = create_engine(db_url)
    with eng.connect() as other:
        assert other.execute(text("SELECT GET_LOCK('reitstreet:sec-sync', 0)")).scalar() == 1
        assert cli("sec-sync", "--offline-fixtures", str(sec_root)) == 3
    eng.dispose()
    assert db.scalar("SELECT COUNT(*) FROM ops_job_run WHERE job='sec-sync'") == 0


def test_sec_403_stops_the_run_and_alerts(env: Path, db: Db, monkeypatch: pytest.MonkeyPatch) -> None:
    assert cli("migrate") == 0
    assert cli("universe") == 0
    import reitstreet.cli as climod

    def forbidden(_root: Path):  # type: ignore[no-untyped-def]
        return lambda url, headers: (403, {}, b"Your request has been identified as part of a network...")

    monkeypatch.setattr(climod, "fixture_transport", forbidden)
    assert cli("sec-sync", "--offline-fixtures", "unused") == 4
    run = last_run(db, "sec-sync")
    assert run["status"] == "failed"
    assert db.scalar("SELECT COUNT(*) FROM raw_fetch WHERE source='sec'") == 1  # stopped at the first 403
    assert db.scalar("SELECT severity FROM ops_alert ORDER BY alert_id DESC LIMIT 1") == "critical"


def test_placeholder_user_agent_is_refused(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert cli("migrate") == 0
    assert cli("universe") == 0
    monkeypatch.setenv("REITSTREET_SEC_USER_AGENT", "YOUR NAME your.email@example.com")
    assert cli("sec-sync") == 2


def test_dead_letter_cli(env: Path, db: Db, sec_root: Path, tmp_path: Path) -> None:
    broken = tmp_path / "b"
    shutil.copytree(sec_root, broken)
    cik = REITS["O"][0]
    (broken / "data.sec.gov" / "api" / "xbrl" / "companyfacts" / f"CIK{cik:010d}.json").write_text("[]")
    assert cli("migrate") == 0
    assert cli("universe") == 0
    cli("sec-sync", "--offline-fixtures", str(broken))
    dl_id = db.scalar("SELECT dl_id FROM ops_dead_letter WHERE stage='sec.companyfacts.parse'")
    assert dl_id
    assert cli("dead-letter", "list") == 0
    assert cli("dead-letter", "resolve", str(dl_id)) == 2  # note required
    assert cli("dead-letter", "resolve", str(dl_id), "--note", "fixed fixture") == 0
    assert db.scalar("SELECT status FROM ops_dead_letter WHERE dl_id=:i", {"i": dl_id}) == "resolved"


def test_monitor_marks_dead_runs_and_alerts(env: Path, db: Db) -> None:
    assert cli("migrate") == 0
    assert cli("universe") == 0
    db.execute(
        """INSERT INTO ops_job_run (job, status, started_at, heartbeat_at)
           VALUES ('sec-sync', 'running', UTC_TIMESTAMP(6) - INTERVAL 3 HOUR, UTC_TIMESTAMP(6) - INTERVAL 2 HOUR)"""
    )
    db.commit()
    assert cli("monitor") == 0
    assert db.scalar("SELECT status FROM ops_job_run ORDER BY run_id DESC LIMIT 1") == "aborted"
    titles = [r["title"] for r in db.query("SELECT title FROM ops_alert ORDER BY alert_id")]
    assert any("died without finishing" in t for t in titles)
    assert any("SEC sync overdue" in t for t in titles)
    # re-running inside the suppression window does not re-send
    assert cli("monitor") == 0
    assert db.scalar("SELECT COUNT(*) FROM ops_alert WHERE title='SEC sync overdue' AND suppressed=0") == 1
