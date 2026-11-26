"""Replay: rebuild staging from the raw store with no network.

Use after a parser fix or a parser version bump: every SEC response we ever received is in
the raw store, so staging can be re-derived exactly. The replay transport answers each URL
with the newest successful capture of that URL (optionally as of a past time) and 404
otherwise; nothing new is recorded in raw_fetch.
"""

from __future__ import annotations

from datetime import datetime

from ...core.db import Db
from ...core.http import Fetcher, Transport
from ...core.rawstore import RawStore
from . import urls


def replay_transport(db: Db, store: RawStore, as_of: datetime | None = None) -> Transport:
    def _get(url: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
        sha = db.scalar(
            """SELECT sha256 FROM raw_fetch WHERE url_hash = SHA2(:u, 256) AND sha256 IS NOT NULL
                 AND (:t IS NULL OR fetched_at <= :t) ORDER BY fetch_id DESC LIMIT 1""",
            {"u": url, "t": as_of},
        )
        if not sha:
            return 404, {}, b""
        return 200, {"content-type": "application/octet-stream"}, store.get(sha)

    return _get


def replay_fetcher(db: Db, store: RawStore, as_of: datetime | None = None) -> Fetcher:
    return Fetcher(
        source="sec-replay",
        user_agent="replay",
        rps=1000.0,
        store=store,
        db=None,
        transport=replay_transport(db, store, as_of),
        max_retries=0,
    )


def has_capture(db: Db, cik: int) -> bool:
    return bool(
        db.scalar(
            "SELECT 1 FROM raw_fetch WHERE url_hash = SHA2(:u, 256) AND sha256 IS NOT NULL LIMIT 1",
            {"u": urls.submissions_url(cik)},
        )
    )


def reset_index_marks(db: Db, company_id: int) -> int:
    """Let sync re-parse every stored index page for this company."""
    n = db.execute("UPDATE stg_sec_filing SET docs_indexed_at=NULL WHERE company_id=:c", {"c": company_id})
    db.commit()
    return n
