"""Run monitor. Scheduled every 30 minutes (scripts/windows/run_monitor.bat). It only reads
ops_ tables and the data, and raises alerts; it changes nothing except marking runs whose
process died as 'aborted'.

Alerts:
  aborted run      a 'running' row whose heartbeat is older than monitor_stale_heartbeat_minutes
  failed run       any run that finished failed (critical for sec-sync: no new data is arriving)
  partial run      some items failed; details in ops_dead_letter
  overdue job      no successful sec-sync for monitor_max_hours_since_sync hours
  dq errors        the latest dq run has failures of severity error
  dead letters     open dead letters older than 3 days
"""

from __future__ import annotations

from typing import Any

from ..core.alerts import Alerter
from ..core.config import Settings
from ..core.db import Db


def mark_aborted(db: Db, stale_minutes: float) -> list[dict[str, Any]]:
    rows = db.query(
        """SELECT run_id, job, started_at, heartbeat_at FROM ops_job_run WHERE status='running'
           AND COALESCE(heartbeat_at, started_at) < UTC_TIMESTAMP(6) - INTERVAL :m MINUTE""",
        {"m": stale_minutes},
    )
    for r in rows:
        db.execute(
            """UPDATE ops_job_run SET status='aborted', finished_at=UTC_TIMESTAMP(6),
                        error_summary=CONCAT('no heartbeat since ', COALESCE(heartbeat_at, started_at))
                      WHERE run_id=:r AND status='running'""",
            {"r": r["run_id"]},
        )
    db.commit()
    return rows


def run_monitor(db: Db, settings: Settings, alerter: Alerter | None = None, lookback_hours: int = 24) -> dict[str, Any]:
    al = alerter or Alerter(db, settings)
    out: dict[str, Any] = {}

    aborted = mark_aborted(db, settings.monitor_stale_heartbeat_minutes)
    for r in aborted:
        al.raise_alert(
            "error",
            "monitor",
            f"{r['job']} run {r['run_id']} died without finishing",
            f"started {r['started_at']}, last heartbeat {r['heartbeat_at']}. Check the log file for "
            f"that run, then rerun with --resume {r['run_id']}.",
            f"aborted:{r['run_id']}",
        )
    out["aborted"] = len(aborted)

    bad = db.query(
        """SELECT run_id, job, status, items_failed, items_total, error_summary FROM ops_job_run
           WHERE status IN ('failed','partial') AND finished_at > UTC_TIMESTAMP(6) - INTERVAL :h HOUR
           ORDER BY run_id""",
        {"h": lookback_hours},
    )
    for r in bad:
        sev = (
            "critical"
            if r["status"] == "failed" and r["job"] == "sec-sync"
            else ("error" if r["status"] == "failed" else "warn")
        )
        al.raise_alert(
            sev,
            "monitor",
            f"{r['job']} run {r['run_id']} {r['status']} ({r['items_failed']} of {r['items_total']} items failed)",
            (r["error_summary"] or "")[:4000],
            f"run:{r['run_id']}",
        )
    out["failed_or_partial"] = len(bad)

    last_ok = db.scalar(
        "SELECT MAX(finished_at) FROM ops_job_run WHERE job='sec-sync' AND status IN ('succeeded','partial')"
    )
    hours = db.scalar("SELECT TIMESTAMPDIFF(HOUR, :t, UTC_TIMESTAMP())", {"t": last_ok}) if last_ok else None
    has_companies = bool(db.scalar("SELECT COUNT(*) FROM ref_company WHERE in_universe=1"))
    if has_companies and (hours is None or hours > settings.monitor_max_hours_since_sync):
        al.raise_alert(
            "error",
            "monitor",
            "SEC sync overdue",
            f"last successful sec-sync: {last_ok or 'never'}. Expected within "
            f"{settings.monitor_max_hours_since_sync} hours. Is the scheduled task running?",
            "overdue:sec-sync",
        )
    out["hours_since_sync"] = hours

    dq_run = db.scalar("SELECT MAX(run_id) FROM ops_dq_result")
    dq_err = (
        db.query(
            """SELECT check_code, COUNT(*) n FROM ops_dq_result WHERE run_id=:r AND severity='error'
           AND status IN ('fail','error') AND subject <> 'summary' GROUP BY check_code""",
            {"r": dq_run},
        )
        if dq_run
        else []
    )
    crashed = (
        db.query("SELECT check_code FROM ops_dq_result WHERE run_id=:r AND status='error'", {"r": dq_run})
        if dq_run
        else []
    )
    if dq_err or crashed:
        body = "\n".join(
            [f"{r['check_code']}: {r['n']} failures" for r in dq_err]
            + [f"{r['check_code']}: check crashed" for r in crashed]
        )
        al.raise_alert("error", "dq", f"Data quality errors in dq run {dq_run}", body, f"dq:{dq_run}")
    out["dq_error_checks"] = len(dq_err) + len(crashed)

    old_dl = db.query("""SELECT stage, COUNT(*) n FROM ops_dead_letter WHERE status='open'
                         AND first_seen_at < UTC_TIMESTAMP(6) - INTERVAL 3 DAY GROUP BY stage""")
    if old_dl:
        al.raise_alert(
            "warn",
            "monitor",
            f"{sum(r['n'] for r in old_dl)} dead letters open for 3+ days",
            "\n".join(f"{r['stage']}: {r['n']}" for r in old_dl),
            "deadletters:old",
        )
    out["old_dead_letters"] = sum(r["n"] for r in old_dl)
    out.update(ir_alerts(db, settings, al, lookback_hours))
    out["alerts_sent"] = len(al.sent)
    return out


def ir_alerts(db: Db, settings: Settings, al: Alerter, lookback_hours: int = 24) -> dict[str, Any]:
    """IR website alerts: a results page that stopped listing supplementals (layout break),
    a page or PDF URL failing run after run, and supplementals that are late."""
    from ..sources.ir.expectations import expected_supplementals

    breaks = db.query(
        """SELECT c.ticker, k.note, s.url FROM ops_ir_check k JOIN ref_company c ON c.company_id = k.company_id
           JOIN ref_ir_source s ON s.source_id = k.source_id
           WHERE k.outcome='layout_break' AND k.checked_at > UTC_TIMESTAMP(6) - INTERVAL :h HOUR
             AND k.check_id = (SELECT MAX(k2.check_id) FROM ops_ir_check k2 WHERE k2.source_id = k.source_id)""",
        {"h": lookback_hours},
    )
    for r in breaks:
        al.raise_alert(
            "warn",
            "ir",
            f"{r['ticker']} IR page layout changed: no supplemental links found",
            f"{r['url']}\n{r['note'] or ''}\nCheck the page in a browser. If it moved, update the universe file; "
            "if it now needs JavaScript, set strategy=headless in ir_profiles.csv.",
            f"ir-layout:{r['ticker']}",
        )
    failing = db.query(
        """SELECT c.ticker, s.url, s.source_type, s.consecutive_failures, s.last_outcome FROM ref_ir_source s
           JOIN ref_company c ON c.company_id = s.company_id
           WHERE c.in_universe=1 AND s.is_active=1 AND s.source_type IN ('results_page','supplemental_pdf')
             AND s.consecutive_failures >= :n""",
        {"n": settings.ir_alert_after_failures},
    )
    for r in failing:
        al.raise_alert(
            "warn",
            "ir",
            f"{r['ticker']} IR {r['source_type']} failing ({r['consecutive_failures']} checks in a row)",
            f"{r['url']}\nlast outcome: {r['last_outcome']}. See raw_fetch for the HTTP status.",
            f"ir-fail:{r['ticker']}:{r['source_type']}",
        )
    overdue = [e for e in expected_supplementals(db, grace_days=settings.ir_supplemental_grace_days)
               if e.status == "overdue"]  # fmt: skip
    if overdue:
        al.raise_alert(
            "warn",
            "ir",
            f"{len(overdue)} supplementals overdue",
            "\n".join(f"{e.ticker}: {e.reason}" for e in overdue),
            f"ir-overdue:{','.join(sorted(e.ticker for e in overdue))[:150]}",
        )
    return {"ir_layout_breaks": len(breaks), "ir_failing_sources": len(failing), "ir_overdue": len(overdue)}
