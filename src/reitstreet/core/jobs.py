"""Job runner: run bookkeeping, single-instance locks, per-item checkpoints,
dead-lettering and resume.

    with JobRun.start(db, "sec-sync", args) as run:
        for company in companies:
            key = f"cik:{company['cik']}"
            if run.skip_if_resumed(key):
                continue
            with run.item(key) as item:
                ...                       # an exception here fails this item only
                item.rows += n
    # run.status is succeeded / partial / failed, alerts raised by the caller

Rules:
  * One instance per job name (MySQL GET_LOCK on a dedicated connection). A second
    instance exits immediately with LockHeldError instead of corrupting state.
  * An exception inside run.item() is recorded on ops_job_item, sent to the
    dead-letter queue and the loop continues. FatalSourceError is re-raised: the
    whole run stops (e.g. SEC 403) so we do not hammer a source that refuses us.
  * --resume <run_id>: items that finished ok in that run are skipped.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .. import __version__
from .db import Db
from .errors import FatalSourceError, LockHeldError
from .logging import run_context

log = logging.getLogger(__name__)


def code_version() -> str:
    sha = os.environ.get("GITHUB_SHA", "")
    if not sha:
        try:
            sha = subprocess.run(
                ["git", "rev-parse", "--short=12", "HEAD"],  # noqa: S607
                capture_output=True,
                text=True,
                timeout=5,
                cwd=Path(__file__).resolve().parent,
                check=False,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            sha = ""
    return f"{__version__}+{sha[:12]}" if sha else __version__


def dead_letter(
    db: Db,
    *,
    stage: str,
    item_key: str,
    exc: BaseException,
    run_id: int | None,
    sha256: str | None = None,
    company_id: int | None = None,
    doc_id: int | None = None,
) -> None:
    """Record a failure in the QA queue. Repeated failures bump occurrences on the open row."""
    db.execute(
        """INSERT INTO ops_dead_letter (stage, item_key, sha256, company_id, doc_id, error_class, error_message,
                                        traceback, first_run_id, last_run_id)
           VALUES (:st, :k, :sha, :c, :d, :cls, :msg, :tb, :run, :run) AS new
           ON DUPLICATE KEY UPDATE occurrences = ops_dead_letter.occurrences + 1, last_run_id = new.last_run_id,
             last_seen_at = CURRENT_TIMESTAMP(6), error_message = new.error_message, traceback = new.traceback""",
        {
            "st": stage[:64],
            "k": item_key[:191],
            "sha": sha256,
            "c": company_id,
            "d": doc_id,
            "cls": type(exc).__name__[:128],
            "msg": str(exc)[:60000],
            "tb": "".join(traceback.format_exception(exc))[:1_000_000],
            "run": run_id,
        },
    )
    db.commit()


def resolve_dead_letters(db: Db, *, stage: str, item_key: str, note: str) -> int:
    n = db.execute(
        """UPDATE ops_dead_letter SET status='resolved', resolved_at=CURRENT_TIMESTAMP(6), resolution_note=:n
           WHERE stage=:s AND item_key=:k AND status='open'""",
        {"s": stage, "k": item_key, "n": note},
    )
    db.commit()
    return n


@dataclass
class Item:
    key: str
    rows: int = 0
    skipped: bool = False
    detail: str | None = None
    stage: str = ""
    sha256: str | None = None
    company_id: int | None = None
    doc_id: int | None = None


@dataclass
class JobRun:
    db: Db
    job: str
    run_id: int
    lock_conn: Connection | None
    done_in_resumed: set[str] = field(default_factory=set)
    ok: int = 0
    failed: int = 0
    skipped: int = 0
    rows: int = 0
    http_requests: int = 0
    errors: list[str] = field(default_factory=list)
    status: str = "running"

    # ---------------------------------------------------------------- lifecycle
    @classmethod
    @contextmanager
    def start(
        cls, db: Db, job: str, args: dict[str, Any] | None = None, *, resume_from: int | None = None, lock: bool = True
    ) -> Iterator[JobRun]:
        lock_conn = None
        if lock:
            lock_conn = db.engine.connect()
            got = lock_conn.execute(text("SELECT GET_LOCK(:n, 0)"), {"n": f"reitstreet:{job}"}).scalar()
            if got != 1:
                lock_conn.close()
                raise LockHeldError(f"another '{job}' run is in progress (lock reitstreet:{job} is held)")
        run_id = db.execute(
            """INSERT INTO ops_job_run (job, args, host, pid, code_version, resumed_from_run_id, heartbeat_at)
               VALUES (:j, :a, :h, :p, :v, :r, CURRENT_TIMESTAMP(6))""",
            {
                "j": job,
                "a": json.dumps(args or {}, default=str),
                "h": socket.gethostname()[:128],
                "p": os.getpid(),
                "v": code_version(),
                "r": resume_from,
            },
        )
        db.commit()
        run = cls(db=db, job=job, run_id=run_id, lock_conn=lock_conn)
        if resume_from:
            run.done_in_resumed = {
                r["item_key"]
                for r in db.query(
                    "SELECT item_key FROM ops_job_item WHERE run_id=:r AND status IN ('ok','skipped')",
                    {"r": resume_from},
                )
            }
        token = run_context.set({"job": job, "run_id": run_id})
        log.info("run started", extra={"job_args": args or {}, "resume_from": resume_from})
        try:
            yield run
        except FatalSourceError as e:
            run.errors.insert(0, f"FATAL: {e}")
            run._finish("failed")
            raise
        except BaseException as e:
            run.errors.insert(0, f"{type(e).__name__}: {e}")
            run._finish("failed")
            raise
        else:
            run._finish(None)
        finally:
            run_context.reset(token)
            if lock_conn is not None:
                try:
                    lock_conn.execute(text("SELECT RELEASE_LOCK(:n)"), {"n": f"reitstreet:{job}"})
                finally:
                    lock_conn.close()

    def _finish(self, forced: str | None) -> None:
        try:
            self.db.rollback()
        except Exception:  # noqa: S110  # connection may be gone; the update below reports it
            pass
        if forced:
            status = forced
        elif self.failed and not self.ok:
            status = "failed"
        elif self.failed:
            status = "partial"
        else:
            status = "succeeded"
        self.status = status
        self.db.execute(
            """UPDATE ops_job_run SET status=:s, finished_at=CURRENT_TIMESTAMP(6), heartbeat_at=CURRENT_TIMESTAMP(6),
                 items_total=:t, items_ok=:ok, items_failed=:f, items_skipped=:sk, http_requests=:h,
                 rows_written=:rw, error_summary=:e WHERE run_id=:r""",
            {
                "s": status,
                "t": self.ok + self.failed + self.skipped,
                "ok": self.ok,
                "f": self.failed,
                "sk": self.skipped,
                "h": self.http_requests,
                "rw": self.rows,
                "e": "\n".join(self.errors[:20])[:60000] or None,
                "r": self.run_id,
            },
        )
        self.db.commit()
        log.info(
            "run finished",
            extra={
                "status": status,
                "ok": self.ok,
                "failed": self.failed,
                "skipped": self.skipped,
                "rows": self.rows,
                "http": self.http_requests,
            },
        )

    def heartbeat(self) -> None:
        self.db.execute(
            """UPDATE ops_job_run SET heartbeat_at=CURRENT_TIMESTAMP(6), items_ok=:ok, items_failed=:f,
                 items_skipped=:sk, rows_written=:rw, http_requests=:h WHERE run_id=:r""",
            {
                "ok": self.ok,
                "f": self.failed,
                "sk": self.skipped,
                "rw": self.rows,
                "h": self.http_requests,
                "r": self.run_id,
            },
        )
        self.db.commit()

    # ---------------------------------------------------------------- items
    def skip_if_resumed(self, key: str) -> bool:
        """True (and recorded as skipped) if this item finished ok in the run being resumed."""
        if key not in self.done_in_resumed:
            return False
        self.skipped += 1
        self._record_item(Item(key=key), "skipped", "done in resumed run")
        return True

    @contextmanager
    def item(self, key: str, *, stage: str = "", company_id: int | None = None) -> Iterator[Item]:
        it = Item(key=key, stage=stage or self.job, company_id=company_id)
        self.db.execute(
            "INSERT INTO ops_job_item (run_id, item_key, status) VALUES (:r, :k, 'running') "
            "ON DUPLICATE KEY UPDATE status='running', started_at=CURRENT_TIMESTAMP(6)",
            {"r": self.run_id, "k": key},
        )
        self.db.commit()
        try:
            yield it
        except FatalSourceError:
            self.db.rollback()
            self.failed += 1
            self._record_item(it, "failed", "fatal source error, run stopped")
            raise
        except Exception as e:
            self.db.rollback()
            self.failed += 1
            msg = f"{type(e).__name__}: {e}"
            self.errors.append(f"{key}: {msg}")
            log.exception("item failed", extra={"item": key, "stage": it.stage})
            self._record_item(it, "failed", msg)
            dead_letter(
                self.db,
                stage=it.stage,
                item_key=key,
                exc=e,
                run_id=self.run_id,
                sha256=it.sha256,
                company_id=it.company_id,
                doc_id=it.doc_id,
            )
        else:
            self.db.commit()
            self.rows += it.rows
            if it.skipped:
                self.skipped += 1
                self._record_item(it, "skipped", it.detail)
            else:
                self.ok += 1
                self._record_item(it, "ok", it.detail)
        finally:
            if (self.ok + self.failed + self.skipped) % 10 == 0:
                self.heartbeat()

    def _record_item(self, it: Item, status: str, detail: str | None) -> None:
        self.db.execute(
            """INSERT INTO ops_job_item (run_id, item_key, status, finished_at, rows_written, detail)
               VALUES (:r, :k, :s, CURRENT_TIMESTAMP(6), :n, :d) AS new
               ON DUPLICATE KEY UPDATE status=new.status, finished_at=new.finished_at,
                 rows_written=new.rows_written, detail=new.detail""",
            {"r": self.run_id, "k": it.key, "s": status, "n": it.rows, "d": (detail or "")[:1024] or None},
        )
        self.db.commit()
