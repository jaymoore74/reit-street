"""Download a registered document (any source) into the raw store and de-duplicate
by content: the same PDF published twice (IR site and 8-K, or two archive copies)
is stored once and the later registration is marked duplicate."""

from __future__ import annotations

from typing import Any

from ..core.db import Db
from ..core.http import Fetcher


def mark_fetched(db: Db, doc_id: int, sha: str, nbytes: int) -> str:
    dup = db.scalar(
        """SELECT doc_id FROM stg_document WHERE sha256=:s AND doc_id<>:d AND status<>'duplicate'
           ORDER BY doc_id LIMIT 1""",
        {"s": sha, "d": doc_id},
    )
    status = "duplicate" if dup else "fetched"
    db.execute(
        """UPDATE stg_document SET sha256=:s, bytes=:n, status=:st, duplicate_of_doc_id=:dup,
             status_note=:note WHERE doc_id=:d""",
        {
            "s": sha,
            "n": nbytes,
            "st": status,
            "dup": dup,
            "d": doc_id,
            "note": f"same bytes as doc {dup}" if dup else None,
        },
    )
    return status


def fetch_document(db: Db, fetcher: Fetcher, doc: dict[str, Any]) -> str:
    r = fetcher.get(doc["source_url"], allow_404=True)
    if r is None:
        db.execute(
            "UPDATE stg_document SET status='failed', status_note='404 at source' WHERE doc_id=:d", {"d": doc["doc_id"]}
        )
        return "failed"
    return mark_fetched(db, doc["doc_id"], r.sha256, len(r.body))
