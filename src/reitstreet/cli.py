"""reitstreet command line.  `reitstreet <command> --help` for options.

Exit codes: 0 ok, 1 run failed or partial, 2 configuration error, 3 another run of the
same job holds the lock, 4 a source refused us (e.g. SEC 403: fix the User-Agent).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .core import config as cfgmod
from .core import logging as logsetup
from .core import migrate
from .core.alerts import Alerter
from .core.db import Db, in_clause
from .core.errors import ConfigError, FatalSourceError, LockHeldError, ParseError
from .core.http import Fetcher, HostRateLimiter, fixture_transport
from .core.jobs import JobRun
from .core.rawstore import RawStore
from .sources.sec import urls

log = logging.getLogger("reitstreet")

EXIT_OK, EXIT_RUN, EXIT_CONFIG, EXIT_LOCK, EXIT_FATAL = 0, 1, 2, 3, 4


# --------------------------------------------------------------------------- helpers
class App:
    def __init__(self, settings: cfgmod.Settings, args: argparse.Namespace):
        self.s = settings
        self.a = args
        self._db: Db | None = None
        self._store: RawStore | None = None

    @property
    def db(self) -> Db:
        if self._db is None:
            self._db = Db.from_url(self.s.sqlalchemy_url())
        return self._db

    @property
    def store(self) -> RawStore:
        if self._store is None:
            self._store = RawStore.local(self.s.raw_root)
        return self._store

    def sec_fetcher(self, run_id: int | None) -> Fetcher:
        offline = getattr(self.a, "offline_fixtures", None)
        return Fetcher(
            source="sec",
            user_agent="offline-test test@localhost" if offline else self.s.require_sec_user_agent(),
            rps=1000.0 if offline else self.s.sec_requests_per_second,
            store=self.store,
            db=self.db,
            run_id=run_id,
            transport=fixture_transport(Path(offline)) if offline else None,
            fatal_status=(401, 403),
            fatal_hint=urls.FATAL_HINT,
        )

    def web_fetcher(self, run_id: int | None) -> Fetcher:
        """IR websites: generic honest User-Agent, per-host rate limit, robots.txt (see sources.ir.robots)."""
        offline = getattr(self.a, "offline_fixtures", None)
        rps = 1000.0 if offline else self.s.ir_requests_per_second
        return Fetcher(
            source="ir_website",
            user_agent=self.s.web_user_agent(__version__),
            rps=rps,
            store=self.store,
            db=self.db,
            run_id=run_id,
            transport=fixture_transport(Path(offline)) if offline else None,
            limiter=HostRateLimiter(rps),
            max_retries=3,
        )

    def companies(self, tickers: list[str] | None = None, *, all_companies: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM ref_company WHERE 1=1"
        params: dict[str, Any] = {}
        if not all_companies:
            sql += " AND in_universe=1"
        if tickers:
            clause, params = in_clause("t", tickers)
            sql += f" AND ticker IN {clause}"
        rows = [dict(r) for r in self.db.query(sql + " ORDER BY ticker", params)]
        if tickers:
            missing = sorted(set(tickers) - {r["ticker"] for r in rows})
            if missing:
                log.warning("tickers not in ref_company", extra={"tickers": ",".join(missing)})
        return rows

    def close(self) -> None:
        if self._db is not None:
            self._db.close()


def _list(s: str | None) -> list[str] | None:
    return [t.strip().upper() for t in s.split(",") if t.strip()] if s else None


def _run_exit(run: JobRun) -> int:
    return EXIT_OK if run.status == "succeeded" else EXIT_RUN


def _alert_run(app: App, run: JobRun) -> None:
    if run.status in ("failed", "partial"):
        sev = (
            "critical"
            if run.status == "failed" and run.job == "sec-sync"
            else ("error" if run.status == "failed" else "warn")
        )
        Alerter(app.db, app.s).raise_alert(
            sev,
            run.job,
            f"{run.job} run {run.run_id} {run.status} ({run.failed} failed, {run.ok} ok)",
            "\n".join(run.errors[:20]),
            f"run:{run.run_id}",
        )


def _job(app: App, name: str, body: Callable[[JobRun], None]) -> int:
    args = {k: v for k, v in vars(app.a).items() if k not in ("func", "config")}
    resume = getattr(app.a, "resume", None)
    try:
        with JobRun.start(app.db, name, args, resume_from=resume) as run:
            body(run)
    except FatalSourceError as e:
        log.error(str(e))
        Alerter(app.db, app.s).raise_alert(
            "critical", name, f"{name} stopped: source refused requests", str(e), f"fatal:{name}"
        )
        return EXIT_FATAL
    _alert_run(app, run)
    print(
        json.dumps(
            {
                "run_id": run.run_id,
                "status": run.status,
                "ok": run.ok,
                "failed": run.failed,
                "skipped": run.skipped,
                "rows": run.rows,
                "http_requests": run.http_requests,
            }
        )
    )
    return _run_exit(run)


# --------------------------------------------------------------------------- commands
def cmd_config(app: App) -> int:
    print(json.dumps(app.s.redacted(), indent=2, default=str))
    return EXIT_OK


def cmd_migrate(app: App) -> int:
    url = app.s.sqlalchemy_url()
    before = migrate.current_revision(url)
    if app.a.downgrade:
        migrate.downgrade(url, app.a.downgrade)
    else:
        migrate.upgrade(url, app.a.revision)
    after = migrate.current_revision(url)
    print(json.dumps({"from": before, "to": after, "head": migrate.head_revision()}))
    if not app.a.downgrade and not app.a.no_seed and after == migrate.head_revision():
        return cmd_seed(app)
    return EXIT_OK


def cmd_seed(app: App) -> int:
    from .properties.sources import apply_sources
    from .reference.seed import seed_reference
    from .sources.ir.profiles import apply_profiles

    print(json.dumps({**seed_reference(app.db), **apply_profiles(app.db), **apply_sources(app.db)}))
    return EXIT_OK


def cmd_universe(app: App) -> int:
    from .properties.sources import apply_sources
    from .reference.universe import import_universe
    from .sources.ir.profiles import apply_profiles

    path = Path(app.a.csv) if app.a.csv else app.s.universe_csv
    if not path or not Path(path).exists():
        raise ConfigError(f"Universe CSV not found: {path}. Set [paths] universe_csv or pass --csv.")

    def body(run: JobRun) -> None:
        with run.item(f"file:{Path(path).name}", stage="universe") as it:
            fetcher = app.sec_fetcher(run.run_id) if app.a.resolve_ciks else None
            res = import_universe(app.db, app.store, Path(path), run_id=run.run_id, fetcher=fetcher, prune=app.a.prune)
            res.update(apply_profiles(app.db))
            res.update(apply_sources(app.db))
            it.rows = int(str(res["companies"]))
            it.detail = json.dumps(res, default=str)
            if res["unresolved"]:
                run.errors.append(f"tickers without CIK: {res['unresolved']}")
            log.info("universe imported", extra=res)

    return _job(app, "universe", body)


def cmd_sec_sync(app: App) -> int:
    from .sources.sec.loaders import ConceptCache
    from .sources.sec.sync import SyncOptions, sync_company
    from .warehouse.metrics import build_company

    a = app.a
    opt = SyncOptions(
        full_history=a.full_history,
        force=a.force,
        index_docs=not a.no_docs,
        download_exhibits=app.s.sec_download_exhibits and not a.no_download,
        doc_limit=a.doc_limit,
        facts=not a.no_facts,
    )
    comps = app.companies(_list(a.tickers))
    if not comps:
        raise ConfigError("No companies in the universe. Run: reitstreet universe")

    def body(run: JobRun) -> None:
        f = app.sec_fetcher(run.run_id)
        cache = ConceptCache(app.db)
        for c in comps:
            key = f"cik:{c['cik']}"
            if run.skip_if_resumed(key):
                continue
            with run.item(key, stage="sec.sync", company_id=c["company_id"]) as it:
                try:
                    r = sync_company(app.db, f, c, opt, run.run_id, cache)
                    built = build_company(app.db, c, run.run_id) if (r.warehouse_dirty and not a.no_build) else None
                finally:
                    run.http_requests = f.request_count
                it.rows = r.rows + (built["inserted"] + built["retired"] if built else 0)
                it.detail = (
                    f"filings+{r.new_filings} indexed {r.indexed} docs+{r.new_docs} dl {r.downloaded} "
                    f"facts {r.facts_status}+{r.new_facts} parse_errors {r.parse_errors}"
                    + (f" metrics +{built['inserted']}/-{built['retired']}" if built else "")
                )
                log.info("company synced", extra={"ticker": c["ticker"], "detail": it.detail})

    return _job(app, "sec-sync", body)


def cmd_build_metrics(app: App) -> int:
    from .warehouse.metrics import build_company

    comps = app.companies(_list(app.a.tickers))

    def body(run: JobRun) -> None:
        for c in comps:
            key = f"cik:{c['cik']}"
            if run.skip_if_resumed(key):
                continue
            with run.item(key, stage="warehouse.build", company_id=c["company_id"]) as it:
                r = build_company(app.db, c, run.run_id)
                it.rows = r["inserted"] + r["retired"]
                it.detail = json.dumps(r)

    return _job(app, "build-metrics", body)


def cmd_parse_docs(app: App) -> int:
    from .parsers.run import DOC_COLS, parse_document
    from .warehouse.metrics import build_company

    statuses = ["fetched"] + (["parsed", "needs_review", "failed"] if app.a.reparse else [])
    sc, sp = in_clause("st", statuses)
    sql = f"SELECT {DOC_COLS} FROM stg_document d WHERE d.status IN {sc} AND d.sha256 IS NOT NULL"
    params: dict[str, Any] = dict(sp)
    comps = app.companies(_list(app.a.tickers)) if app.a.tickers else None
    if comps is not None:
        cc, cp = in_clause("c", [c["company_id"] for c in comps] or [0])
        sql += f" AND d.company_id IN {cc}"
        params.update(cp)
    docs = [
        dict(r)
        for r in app.db.query(
            sql + " ORDER BY d.doc_id" + (f" LIMIT {int(app.a.limit)}" if app.a.limit else ""), params
        )
    ]

    def body(run: JobRun) -> None:
        touched: set[int] = set()
        for d in docs:
            key = f"doc:{d['doc_id']}"
            if run.skip_if_resumed(key):
                continue
            with run.item(key, stage="document.parse", company_id=d["company_id"]) as it:
                it.doc_id, it.sha256 = d["doc_id"], d["sha256"]
                try:
                    status = parse_document(app.db, app.store, d, run.run_id)
                except ParseError:
                    app.db.commit()  # keep the failed stg_doc_parse row and document status
                    raise
                it.detail = status
                if status == "no_parser":
                    it.skipped = True
                elif status == "parsed" and d["company_id"]:
                    touched.add(d["company_id"])
        if not app.a.no_build:
            for c in app.companies(all_companies=True):
                if c["company_id"] in touched:
                    with run.item(f"build:cik:{c['cik']}", stage="warehouse.build", company_id=c["company_id"]) as it:
                        r = build_company(app.db, c, run.run_id)
                        it.rows = r["inserted"] + r["retired"]

    return _job(app, "parse-docs", body)


def cmd_import_archive(app: App) -> int:
    from .sources.supplementals.archive import import_archive

    folder = Path(app.a.folder)
    if not folder.is_dir():
        raise ConfigError(f"Not a folder: {folder}")

    def body(run: JobRun) -> None:
        with run.item(f"folder:{folder.resolve().as_posix()}", stage="archive.import") as it:
            res = import_archive(
                app.db, app.store, folder, run_id=run.run_id, manifest=Path(app.a.manifest) if app.a.manifest else None
            )
            it.rows = res["new"] + res["needs_review"]
            it.detail = json.dumps({k: v for k, v in res.items() if k != "review_files"})
            print(json.dumps(res, indent=2))

    return _job(app, "import-archive", body)


def _ir_kinds(s: str | None) -> frozenset[str]:
    from .sources.ir.models import DOC_KINDS

    kinds = frozenset(k.strip().lower() for k in (s or "").split(",") if k.strip())
    bad = sorted(kinds - set(DOC_KINDS))
    if bad:
        raise SystemExit(f"--kinds: unknown {', '.join(bad)}; choose from {', '.join(DOC_KINDS)}")
    return kinds


def cmd_fetch_ir(app: App) -> int:
    from .sources.ir.fetch import IrOptions, fetch_company
    from .sources.ir.robots import RobotsCache
    from .sources.ir.strategies import q4_key_for

    a = app.a
    comps = app.companies(_list(a.tickers))
    opts = IrOptions(
        download=not a.no_download,
        kinds=_ir_kinds(a.kinds) or app.s.ir_kinds(),
        max_new_docs=app.s.ir_max_new_docs_per_company,
        backfill=a.backfill,
        strategy=a.strategy,
        headless=app.s.ir_headless or a.headless,
        conditional=not a.force,
        layout_break_min_links=app.s.ir_layout_break_min_links,
    )

    def body(run: JobRun) -> None:
        f = app.web_fetcher(run.run_id)
        ua = f.user_agent
        robots = RobotsCache(f, app.db, agent_token=ua.split("/")[0].split()[0], ttl_hours=app.s.ir_robots_ttl_hours)
        for c in comps:
            key = f"cik:{c['cik']}"
            if run.skip_if_resumed(key):
                continue
            with run.item(key, stage="ir.fetch", company_id=c["company_id"]) as it:
                r = fetch_company(app.db, f, robots, c, run.run_id, opts, q4_key=q4_key_for(c["ticker"], app.s.extra))
                run.http_requests = f.request_count
                it.rows = r.new_docs
                it.skipped = r.pages == 0 and not r.direct.get("checked")
                it.detail = json.dumps(r.as_dict())

    return _job(app, "fetch-ir", body)


def cmd_ir_coverage(app: App) -> int:
    from .sources.ir.coverage import coverage_rows, summarize, write_csv
    from .sources.ir.expectations import expected_supplementals

    rows = coverage_rows(app.db)
    exp = {e.ticker: e for e in expected_supplementals(app.db, grace_days=app.s.ir_supplemental_grace_days)}
    if app.a.out:
        write_csv(rows, Path(app.a.out))
    print(json.dumps({"companies": len(rows), "coverage": summarize(rows)}))
    overdue = [e for e in exp.values() if e.status == "overdue"]
    for e in overdue:
        print(f"OVERDUE {e.ticker:<6} {e.reason}")
    if app.a.verbose_rows:
        for r in rows:
            supp = r["last_supp_links"] or r["probe_supp_links"] or 0
            print(
                f"{r['ticker']:<6} {r['coverage']:<15} {r['strategy']:<12} {r['platform'] or '':<12} "
                f"{r['last_outcome'] or r['probe_status']:<14} supp={supp}"
            )
    return EXIT_OK


def cmd_replay(app: App) -> int:
    from .sources.sec.loaders import ConceptCache
    from .sources.sec.replay import has_capture, replay_fetcher, reset_index_marks
    from .sources.sec.sync import SyncOptions, sync_company
    from .warehouse.metrics import build_company

    as_of = datetime.fromisoformat(app.a.as_of) if app.a.as_of else None
    comps = app.companies(_list(app.a.tickers))
    opt = SyncOptions(full_history=True, force=True, index_docs=app.a.reindex, download_exhibits=False)

    def body(run: JobRun) -> None:
        f = replay_fetcher(app.db, app.store, as_of)
        cache = ConceptCache(app.db)
        for c in comps:
            key = f"cik:{c['cik']}"
            if run.skip_if_resumed(key):
                continue
            with run.item(key, stage="sec.replay", company_id=c["company_id"]) as it:
                if not has_capture(app.db, int(c["cik"])):
                    it.skipped, it.detail = True, "nothing captured yet"
                    continue
                if app.a.reindex:
                    reset_index_marks(app.db, c["company_id"])
                r = sync_company(app.db, f, c, opt, run.run_id, cache)
                built = build_company(app.db, c, run.run_id) if not app.a.no_build else None
                it.rows = r.rows + (built["inserted"] + built["retired"] if built else 0)
                it.detail = f"facts {r.facts_status} parse_errors {r.parse_errors}" + (
                    f" metrics +{built['inserted']}/-{built['retired']}" if built else ""
                )

    return _job(app, "replay", body)


def cmd_dq(app: App) -> int:
    from .quality.checks import Ctx, run_checks

    def body(run: JobRun) -> None:
        with run.item("dq", stage="dq") as it:
            only = [x.lower() for x in _list(app.a.only) or []] or None
            summary = run_checks(
                app.db,
                Ctx(
                    store=app.store,
                    supplemental_grace_days=app.s.ir_supplemental_grace_days,
                    ir_failures_threshold=app.s.ir_alert_after_failures,
                ),
                run.run_id,
                only=only,
            )
            it.rows = sum(s["findings"] for s in summary)
            for s in summary:
                print(
                    f"{s['status']:<5} {s['severity']:<5} {s['check']:<34} {s['findings']:>6}"
                    + (f"  {s['error']}" if s["error"] else (f"  e.g. {s['sample'][0]}" if s["sample"] else ""))
                )
            bad = [s for s in summary if s["status"] == "error" or (s["status"] == "fail" and s["severity"] == "error")]
            if bad:
                Alerter(app.db, app.s).raise_alert(
                    "error",
                    "dq",
                    f"Data quality: {len(bad)} checks failing at error level",
                    "\n".join(f"{s['check']}: {s['findings']} {s['error'] or ''}" for s in bad),
                    f"dq:{run.run_id}",
                )

    return _job(app, "dq", body)


def cmd_monitor(app: App) -> int:
    from .quality.monitor import run_monitor

    res = run_monitor(app.db, app.s)
    print(json.dumps(res, default=str))
    return EXIT_OK


def cmd_export(app: App) -> int:
    from .delivery.export import export

    a = app.a
    views = [v.lower() for v in _list(a.views) or []] or None
    res = export(
        app.db,
        Path(a.out) if a.out else app.s.data_dir / "exports",
        views=views,
        tickers=_list(a.tickers),
        metrics=_list(a.metrics),
        since=date.fromisoformat(a.since) if a.since else None,
        as_of_date=date.fromisoformat(a.as_of_date) if a.as_of_date else None,
        as_recorded=datetime.fromisoformat(a.as_recorded) if a.as_recorded else None,
    )
    for name, f in res["files"].items():
        print(f"{name:<16} {f['rows']:>9,} rows  {f['file']}")
    print(res["dir"])
    return EXIT_OK


SPOT_METRICS = ("TOTAL_REVENUE", "NET_INCOME", "TOTAL_ASSETS", "TOTAL_DEBT", "FFO_NAREIT", "FFO_PER_SHARE")


def cmd_spotcheck(app: App) -> int:
    for c in app.companies(_list(app.a.tickers)):
        print(f"\n{c['ticker']}  {c['name']}  CIK {c['cik']}  FYE {c['fiscal_year_end']}")
        for code in SPOT_METRICS:
            r = app.db.one(
                """SELECT period_type, period_end, value, unit, version, source_type, source_accession,
                          source_filing_url, source_document_url, source_page_no
                   FROM dl_metrics WHERE ticker=:t AND metric_code=:m AND segment='Total' AND is_preferred=1
                     AND period_type IN ('FY','INSTANT') ORDER BY period_end DESC LIMIT 1""",
                {"t": c["ticker"], "m": code},
            )
            if not r:
                print(f"  {code:<15} (none{': comes from supplementals' if code.startswith('FFO') else ''})")
                continue
            val = r["value"]
            shown = f"{val / 1_000_000:,.1f}m" if r["unit"] == "USD" else f"{val:,.4f}"
            src = r["source_filing_url"] or r["source_document_url"] or ""
            page = f" p.{r['source_page_no']}" if r["source_page_no"] else ""
            print(
                f"  {code:<15} {r['period_type']:<7} {r['period_end']} {shown:>14} {r['unit']:<10} v{r['version']} "
                f"{r['source_type']:<12} {r['source_accession'] or ''}{page}  {src}"
            )
    return EXIT_OK


def cmd_status(app: App) -> int:
    db = app.db
    print(
        f"reitstreet {__version__}  schema {migrate.current_revision(app.s.sqlalchemy_url())} "
        f"(head {migrate.head_revision()})"
    )
    for t in (
        "ref_company",
        "stg_sec_filing",
        "stg_xbrl_fact",
        "stg_document",
        "stg_doc_line_item",
        "wh_metric_value",
        "raw_object",
        "raw_fetch",
    ):
        print(f"  {t:<22}{db.scalar(f'SELECT COUNT(*) FROM {t}'):>12,}")
    print(f"  {'live metric values':<22}{db.scalar('SELECT COUNT(*) FROM wh_metric_value WHERE is_live=1'):>12,}")
    open_dl = db.scalar("SELECT COUNT(*) FROM ops_dead_letter WHERE status='open'")
    print(f"  {'open dead letters':<22}{open_dl:>12,}")
    print("\nlast runs")
    for r in db.query("""SELECT run_id, job, status, started_at, finished_at, items_ok, items_failed, items_skipped,
                                http_requests FROM ops_job_run ORDER BY run_id DESC LIMIT 10"""):
        print(
            f"  {r['run_id']:>6} {r['job']:<15} {r['status']:<10} {r['started_at']:%Y-%m-%d %H:%M} "
            f"ok {r['items_ok']} failed {r['items_failed']} skipped {r['items_skipped']} http {r['http_requests']}"
        )
    return EXIT_OK


def cmd_dead_letter(app: App) -> int:
    db, a = app.db, app.a
    if a.action == "list":
        where = "" if a.all else "WHERE status='open'"
        params: dict[str, Any] = {}
        if a.stage:
            where += (" AND " if where else "WHERE ") + "stage LIKE :s"
            params["s"] = a.stage + "%"
        for r in db.query(
            f"""SELECT dl_id, stage, item_key, status, occurrences, last_seen_at, error_class,
                                    LEFT(error_message, 160) msg FROM ops_dead_letter {where}
                             ORDER BY last_seen_at DESC LIMIT 200""",
            params,
        ):
            print(
                f"{r['dl_id']:>6} {r['status']:<8} {r['stage']:<28} {r['item_key']:<40} x{r['occurrences']} "
                f"{r['last_seen_at']:%Y-%m-%d %H:%M} {r['error_class']}: {r['msg']}"
            )
        return EXIT_OK
    if a.action == "show":
        dl_row = db.one("SELECT * FROM ops_dead_letter WHERE dl_id=:i", {"i": a.id})
        print(json.dumps(dict(dl_row) if dl_row else {}, indent=2, default=str))
        return EXIT_OK
    status = "resolved" if a.action == "resolve" else "ignored"
    n = db.execute(
        """UPDATE ops_dead_letter SET status=:s, resolved_at=CURRENT_TIMESTAMP(6), resolution_note=:n
                      WHERE dl_id=:i AND status='open'""",
        {"s": status, "n": a.note[:1024], "i": a.id},
    )
    db.commit()
    print(f"{n} dead letter(s) marked {status}")
    return EXIT_OK if n else EXIT_RUN


def cmd_db_docs(app: App) -> int:
    from .core.dbdocs import data_dictionary

    text = data_dictionary(app.db)
    Path(app.a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(app.a.out).write_text(text + "\n", encoding="utf-8")
    print(f"wrote {app.a.out} ({text.count(chr(10)) + 1} lines)")
    return EXIT_OK


# --------------------------------------------------------------------------- property master
def cmd_load_properties(app: App) -> int:
    from .properties import lists, schedule_iii
    from .sources.ir.robots import RobotsCache

    a = app.a
    comps = app.companies(_list(a.tickers))
    kinds = set(_list(a.sources) or ["SEC_SCHEDULE_III", "CORPORATE_WEBSITE", "PUBLIC_DATASET"])

    def body(run: JobRun) -> None:
        sec = app.sec_fetcher(run.run_id) if "SEC_SCHEDULE_III" in kinds else None
        web = app.web_fetcher(run.run_id) if kinds & {"CORPORATE_WEBSITE", "PUBLIC_DATASET"} else None
        robots = (
            RobotsCache(
                web, app.db, agent_token=web.user_agent.split("/")[0].split()[0], ttl_hours=app.s.ir_robots_ttl_hours
            )
            if web
            else None
        )
        for c in comps:
            if (c.get("reit_type") or "").lower() == "mortgage":
                continue
            if sec is not None:
                key = f"sched3:{c['cik']}"
                if not run.skip_if_resumed(key):
                    with run.item(key, stage="properties.schedule_iii", company_id=c["company_id"]) as it:
                        r = schedule_iii.load_company(app.db, sec, c, run.run_id, years=a.years, force=a.force)
                        it.rows = r["records"]
                        it.skipped = r["filings"] == 0
                        it.detail = json.dumps(r)
            if web is not None:
                for src in app.db.query(
                    """SELECT * FROM ref_property_source WHERE company_id = :c AND status = 'active'
                       AND list_url IS NOT NULL AND source_kind IN ('corporate_website','public_dataset')""",
                    {"c": c["company_id"]},
                ):
                    if src["source_kind"].upper() not in kinds:
                        continue
                    key = f"plist:{src['source_row_id']}"
                    if run.skip_if_resumed(key):
                        continue
                    with run.item(key, stage="properties.list", company_id=c["company_id"]) as it:
                        r2 = lists.load_source(app.db, web, robots, src, c, run.run_id)
                        it.rows = int(r2.get("records") or 0)
                        it.skipped = "skipped" in r2 or bool(r2.get("unchanged"))
                        it.detail = json.dumps(r2, default=str)
            run.http_requests = (sec.request_count if sec else 0) + (web.request_count if web else 0)
        if not a.no_build:
            from .properties.build import build_properties

            with run.item("build", stage="properties.build") as it:
                st = build_properties(app.db, run.run_id)
                it.rows = st.get("records_to_place", 0)
                it.detail = json.dumps(st)

    return _job(app, "load-properties", body)


def cmd_import_properties(app: App) -> int:
    from .properties.lists import import_file

    a = app.a
    path = Path(a.file)
    if not path.exists():
        raise ConfigError(f"file not found: {path}")
    if not a.ticker and not a.ticker_column:
        raise ConfigError("give --ticker (one company) or --ticker-column (a column with tickers)")
    fmt = a.format or {
        ".csv": "csv",
        ".xlsx": "xlsx",
        ".json": "json",
        ".htm": "html_table",
        ".html": "html_table",
    }.get(path.suffix.lower(), "")
    if not fmt:
        raise ConfigError("cannot tell the format from the file name: pass --format")

    def body(run: JobRun) -> None:
        with run.item(f"file:{path.name}", stage="properties.import") as it:
            r = import_file(
                app.db, app.store, path, fmt=fmt, as_of=date.fromisoformat(a.as_of),
                known_from=date.fromisoformat(a.known_from) if a.known_from else None, source_kind=a.source_kind,
                ticker=a.ticker, ticker_column=a.ticker_column,
                field_map=json.loads(a.field_map) if a.field_map else None, records_path=a.records_path,
                run_id=run.run_id,
            )  # fmt: skip
            it.rows = int(r["records"])
            it.detail = json.dumps(r, default=str)
            print(json.dumps(r, default=str))
            if not a.no_build:
                from .properties.build import build_properties

                print(json.dumps(build_properties(app.db, run.run_id)))

    return _job(app, "import-properties", body)


def cmd_build_properties(app: App) -> int:
    from .properties.build import build_properties

    def body(run: JobRun) -> None:
        with run.item("build", stage="properties.build") as it:
            cids = [c["company_id"] for c in app.companies(_list(app.a.tickers))] if app.a.tickers else None
            st = build_properties(app.db, run.run_id, rematch=app.a.rematch, companies=cids)
            it.rows = st.get("records_to_place", 0)
            it.detail = json.dumps(st)
            print(json.dumps(st))

    return _job(app, "build-properties", body)


def cmd_geocode_properties(app: App) -> int:
    from .properties.geocode import geocode_pending

    def body(run: JobRun) -> None:
        offline = getattr(app.a, "offline_fixtures", None)
        f = Fetcher(
            source="census_geocoder",
            user_agent=app.s.web_user_agent(__version__),
            rps=1000.0 if offline else 2.0,
            store=app.store,
            db=app.db,
            run_id=run.run_id,
            transport=fixture_transport(Path(offline)) if offline else None,
            max_retries=3,
        )
        with run.item("geocode", stage="properties.geocode") as it:
            r = geocode_pending(app.db, f, limit=app.a.limit, run_id=run.run_id)
            run.http_requests = f.request_count
            it.rows = r["match"]
            it.detail = json.dumps(r)

    return _job(app, "geocode-properties", body)


def cmd_property_coverage(app: App) -> int:
    import csv as _csv

    from .properties.coverage import coverage, summary

    rows = coverage(app.db)
    if app.a.out:
        with open(app.a.out, "w", newline="", encoding="utf-8") as fh:
            w = _csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["ticker"])
            w.writeheader()
            w.writerows(rows)
    print(json.dumps(summary(rows), default=str))
    if app.a.verbose_rows:
        for r in rows:
            cov = f"{r['coverage_pct']}%" if r["coverage_pct"] is not None else "n/a"
            print(
                f"{r['ticker']:<6} props={r['properties']:<6} stated={r['stated_count'] or '':<6} {cov:<7} "
                f"{r['sources'] or ''}"
            )
    return EXIT_OK


def cmd_property_review(app: App) -> int:
    from .properties import review

    a = app.a
    if a.action == "list":
        for r in review.queue(app.db, limit=a.limit, ticker=a.ticker):
            cands = ", ".join(f"{c['property_id']} ({c['score']})" for c in r["candidates"] or [])
            print(
                f"record {r['record_id']:<8} {r['ticker']:<6} {r['name'] or ''} | {r['city'] or ''} {r['state'] or ''}"
                f" -> now {r['property_id']}; candidates: {cands}"
            )
        return EXIT_OK
    try:
        if a.action == "confirm":
            print(json.dumps(review.confirm(app.db, a.record, a.property)))
        elif a.action == "reject":
            print(json.dumps(review.reject(app.db, a.record)))
        elif a.action == "merge":
            print(json.dumps(review.merge(app.db, a.property_from, a.property)))
    except ValueError as e:
        app.db.rollback()
        print(f"property-review: {e}", file=sys.stderr)
        return EXIT_CONFIG
    return EXIT_OK


# --------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="reitstreet",
        description="REIT data pipeline: SEC EDGAR and supplementals into a bitemporal MySQL warehouse.",
    )
    p.add_argument("--version", action="version", version=f"reitstreet {__version__}")
    p.add_argument("--config", help="config.ini path (default: REITSTREET_CONFIG or ./config.ini)")
    p.add_argument("--log-format", choices=["json", "text"], help="console log format (files are always JSON)")
    p.add_argument("-v", "--verbose", action="store_true")
    sp = p.add_subparsers(dest="cmd", required=True, metavar="command")

    def cmd(name: str, fn: Callable[[App], int], help_: str) -> argparse.ArgumentParser:
        sub = sp.add_parser(name, help=help_, description=help_)
        sub.set_defaults(func=fn)
        return sub

    def tickers(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--tickers", help="comma-separated tickers (default: whole universe)")

    def resume(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--resume", type=int, metavar="RUN_ID", help="skip items that finished ok in that run")

    def offline(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--offline-fixtures", metavar="DIR", help=argparse.SUPPRESS)

    cmd("config", cmd_config, "print effective settings (secrets masked)")
    m = cmd("migrate", cmd_migrate, "upgrade the schema to the latest revision and seed reference data")
    m.add_argument("--revision", default="head")
    m.add_argument("--downgrade", metavar="REVISION", help="downgrade to REVISION instead (destructive)")
    m.add_argument("--no-seed", action="store_true")
    cmd("seed", cmd_seed, "load the metric dictionary and mapping rules from reference/data")
    u = cmd("universe", cmd_universe, "import the REIT universe CSV into ref_company and ref_ir_source")
    u.add_argument("--csv", help="path to reits.csv (default: [paths] universe_csv)")
    u.add_argument("--prune", action="store_true", help="mark companies missing from the file as out of universe")
    u.add_argument("--resolve-ciks", action="store_true", help="look up missing CIKs in SEC's ticker file")
    offline(u)
    s = cmd("sec-sync", cmd_sec_sync, "pull SEC submissions, filing indexes, 8-K exhibits and XBRL; rebuild metrics")
    tickers(s)
    resume(s)
    offline(s)
    s.add_argument("--full-history", action="store_true", help="also load older paged submissions files")
    s.add_argument("--force", action="store_true", help="reprocess even when SEC content is unchanged")
    s.add_argument("--no-docs", action="store_true", help="skip filing index pages and exhibits")
    s.add_argument("--no-download", action="store_true", help="register exhibits without downloading them")
    s.add_argument("--no-facts", action="store_true", help="skip XBRL companyfacts")
    s.add_argument("--no-build", action="store_true", help="do not rebuild warehouse metrics")
    s.add_argument("--doc-limit", type=int, help="index at most N filings per company this run")
    b = cmd("build-metrics", cmd_build_metrics, "rebuild warehouse metrics from staging (no network)")
    tickers(b)
    resume(b)
    pd = cmd("parse-docs", cmd_parse_docs, "parse fetched documents with the registered parsers")
    tickers(pd)
    resume(pd)
    pd.add_argument("--reparse", action="store_true", help="also re-parse parsed / needs_review / failed documents")
    pd.add_argument("--limit", type=int)
    pd.add_argument("--no-build", action="store_true")
    ia = cmd("import-archive", cmd_import_archive, "import a folder of older supplementals (PDF, XLSX, CSV)")
    ia.add_argument("folder")
    ia.add_argument("--manifest", help="CSV with path,ticker,period_end,published_date,doc_kind")
    fi = cmd("fetch-ir", cmd_fetch_ir, "find and download new supplementals from REIT IR websites")
    tickers(fi)
    resume(fi)
    offline(fi)
    fi.add_argument("--no-download", action="store_true", help="discover and classify links only (a probe)")
    fi.add_argument("--kinds", help="comma-separated doc kinds to download (default: [ir] download_kinds)")
    fi.add_argument("--backfill", action="store_true", help="no per-company cap on downloads this run")
    fi.add_argument("--strategy", choices=["static_html", "q4_feed", "headless"], help="force one strategy")
    fi.add_argument("--headless", action="store_true", help="allow the headless browser this run")
    fi.add_argument("--force", action="store_true", help="ignore ETag / Last-Modified and refetch pages")
    ic = cmd("ir-coverage", cmd_ir_coverage, "per-company IR coverage and overdue supplementals")
    ic.add_argument("--out", help="write the coverage table as CSV")
    ic.add_argument("--rows", dest="verbose_rows", action="store_true", help="print one line per company")
    r = cmd("replay", cmd_replay, "re-parse staging from the raw store, no network (after a parser change)")
    tickers(r)
    resume(r)
    r.add_argument("--as-of", help="use captures made on or before this UTC time (YYYY-MM-DDTHH:MM)")
    r.add_argument("--reindex", action="store_true", help="also re-parse stored filing index pages")
    r.add_argument("--no-build", action="store_true")
    lp = cmd(
        "load-properties",
        cmd_load_properties,
        "load property lists (10-K Schedule III, active website and dataset sources) and rebuild the property master",
    )
    tickers(lp)
    resume(lp)
    offline(lp)
    lp.add_argument(
        "--sources", help="comma-separated: sec_schedule_iii,corporate_website,public_dataset (default all)"
    )
    lp.add_argument("--years", type=int, default=1, help="annual reports per company for Schedule III (default 1)")
    lp.add_argument("--force", action="store_true", help="reload filings already loaded")
    lp.add_argument("--no-build", action="store_true", help="stage records only")
    ip = cmd(
        "import-properties", cmd_import_properties, "stage a property list file (CSV, XLSX, JSON, HTML) and rebuild"
    )
    ip.add_argument("file")
    ip.add_argument("--as-of", required=True, help="YYYY-MM-DD the list describes")
    ip.add_argument("--known-from", help="YYYY-MM-DD it became public (default: as-of)")
    ip.add_argument(
        "--source-kind", default="manual", choices=["manual", "public_dataset", "corporate_website", "supplemental"]
    )
    ip.add_argument("--ticker", help="the company, when the file is one company's list")
    ip.add_argument("--ticker-column", help="column holding the ticker, for files covering several companies")
    ip.add_argument("--format", choices=["csv", "xlsx", "json", "html_table"])
    ip.add_argument("--field-map", help='JSON {"name": "Property", "city": "City", ...}; default: guess from headers')
    ip.add_argument("--records-path", help="JSON path, HTML table index or XLSX sheet name")
    ip.add_argument("--no-build", action="store_true")
    bp = cmd(
        "build-properties",
        cmd_build_properties,
        "match staged property records to stable ids and rebuild ownership (no network)",
    )
    tickers(bp)
    bp.add_argument("--rematch", action="store_true", help="re-link every automatic link (after a matcher change)")
    gp = cmd(
        "geocode-properties", cmd_geocode_properties, "geocode new property addresses (US Census geocoder, cached)"
    )
    gp.add_argument("--limit", type=int)
    offline(gp)
    pc = cmd("property-coverage", cmd_property_coverage, "properties per REIT against the source map")
    pc.add_argument("--out", help="write the table as CSV")
    pc.add_argument("--rows", dest="verbose_rows", action="store_true", help="print one line per company")
    pr = cmd("property-review", cmd_property_review, "list and settle close property matches; merge duplicates")
    pr.add_argument("action", choices=["list", "confirm", "reject", "merge"])
    pr.add_argument("--record", type=int, help="record id (confirm, reject)")
    pr.add_argument("--property", type=int, help="property id it belongs to (confirm) or the survivor (merge)")
    pr.add_argument("--from", dest="property_from", type=int, help="duplicate property id to merge away (merge)")
    pr.add_argument("--ticker")
    pr.add_argument("--limit", type=int, default=50)
    d = cmd("dq", cmd_dq, "run data quality checks")
    d.add_argument("--only", help="comma-separated check code prefixes, e.g. contract,recon")
    cmd("monitor", cmd_monitor, "check run health and send alerts")
    e = cmd("export", cmd_export, "write client CSVs and a manifest")
    tickers(e)
    e.add_argument("--out")
    e.add_argument("--views", help="companies,metrics,metrics_history,filings,documents")
    e.add_argument("--metrics", help="comma-separated metric codes")
    e.add_argument("--since", help="YYYY-MM-DD, period_end filter for metrics")
    e.add_argument("--as-of-date", help="YYYY-MM-DD: metrics as known on that date (knowledge time)")
    e.add_argument("--as-recorded", help="YYYY-MM-DDTHH:MM: metrics as stored in the warehouse then (system time)")
    sc = cmd("spotcheck", cmd_spotcheck, "print key numbers with their source, to compare against filings")
    tickers(sc)
    cmd("status", cmd_status, "row counts and recent runs")
    dl = cmd("dead-letter", cmd_dead_letter, "list, inspect, resolve or ignore failed items")
    dl.add_argument("action", choices=["list", "show", "resolve", "ignore"])
    dl.add_argument("id", nargs="?", type=int)
    dl.add_argument("--note", default="")
    dl.add_argument("--stage")
    dl.add_argument("--all", action="store_true", help="include resolved and ignored")
    mo = sp.add_parser(
        "model",
        help="build a NAV and AFFO model workbook from an inputs file (no database or network)",
        description="Build a NAV and AFFO model workbook (.xlsx, live formulas) from a TOML inputs file.",
    )
    mo.add_argument("action", choices=["build", "check"], help="build: write the workbook; check: validate only")
    mo.add_argument("inputs", help="inputs TOML, e.g. src/reitstreet/models/inputs/ess_2026q3.toml")
    mo.add_argument("--out", help="output .xlsx (default: <TICKER>_nav_affo_<period>.xlsx next to the inputs)")
    dd = cmd("db-docs", cmd_db_docs, "write the data dictionary from the live schema")
    dd.add_argument("--out", default="docs/data-dictionary.md")
    return p


def cmd_model(a: argparse.Namespace) -> int:
    """Standalone: needs no config, database or network."""
    from .models import build_model, load_inputs, write_xlsx

    mi = load_inputs(a.inputs)
    book = build_model(mi)
    problems = book.validate(set(mi.sources))
    if problems:
        for p in problems:
            print(f"model check: {p}", file=sys.stderr)
        return EXIT_CONFIG
    v = book.evaluate()
    years = mi.forecast_years
    print(
        f"{mi.ticker} {mi.period}: NAV/share ${v['nav_ps']:,.2f}, price ${v['nav_price']:,.2f} "
        f"({v['nav_prem']:+.1%}), implied cap {v['nav_imp_cap']:.2%}"
    )
    print("AFFO/share: " + ", ".join(f"{y}E ${v[f'a_affops_{y}']:,.2f}" for y in years))
    if a.action == "check":
        return EXIT_OK
    out = Path(a.out) if a.out else Path(a.inputs).with_name(f"{mi.ticker.lower()}_nav_affo_{mi.period}.xlsx")
    write_xlsx(book, out)
    print(f"wrote {out}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.cmd == "model":
        return cmd_model(a)
    if a.cmd == "dead-letter" and a.action != "list" and a.id is None:
        print("dead-letter show/resolve/ignore needs an id", file=sys.stderr)
        return EXIT_CONFIG
    if a.cmd == "property-review" and (
        (a.action == "confirm" and (a.record is None or a.property is None))
        or (a.action == "reject" and a.record is None)
        or (a.action == "merge" and (a.property is None or a.property_from is None))
    ):
        print(
            "property-review: confirm needs --record and --property, reject --record, merge --from and --property",
            file=sys.stderr,
        )
        return EXIT_CONFIG
    if a.cmd == "dead-letter" and a.action in ("resolve", "ignore") and not a.note:
        print("--note is required: say what was done", file=sys.stderr)
        return EXIT_CONFIG
    try:
        settings = cfgmod.load(a.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return EXIT_CONFIG
    logsetup.setup(
        "DEBUG" if a.verbose else settings.logging_level,
        a.log_format or settings.logging_format,
        settings.data_dir / "logs",
    )
    app = App(settings, a)
    try:
        return int(a.func(app))
    except ConfigError as e:
        log.error("configuration error: %s", e)
        return EXIT_CONFIG
    except LockHeldError as e:
        log.warning(str(e))
        return EXIT_LOCK
    finally:
        app.close()
