"""Bulk import of an archive of older supplementals (PDF, HTML, XLS, CSV).

    reitstreet import-archive D:\\supplementals [--manifest manifest.csv]

Each file is stored byte-for-byte in the raw store and registered in stg_document
(source = manual_archive). Identical files are stored once; repeats are marked
duplicate. Re-running the import on the same folder is a no-op.

Company and period come from, in order:
  1. a manifest CSV: path,ticker,period_end,published_date,doc_kind  (path relative to the folder)
  2. the file name: a known ticker as a separate token, plus a quarter like Q3 2024,
     2024Q3, 3Q24, Q3-24 or FY2024
Files that cannot be identified go to needs_review and the QA queue, never dropped.
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ...core.db import Db
from ...core.http import store_local_file
from ...core.rawstore import RawStore
from ..documents import mark_fetched

log = logging.getLogger(__name__)

EXTS = {
    ".pdf": "application/pdf",
    ".htm": "text/html",
    ".html": "text/html",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
}
QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
_Q_PATTERNS = [
    re.compile(r"(?<![0-9A-Z])Q([1-4])[\s_\-.]*((?:19|20)\d{2})(?!\d)", re.I),  # Q3 2024, Q3_2024
    re.compile(r"(?<!\d)((?:19|20)\d{2})[\s_\-.]*Q([1-4])(?![0-9])", re.I),  # 2024Q3, 2024-Q3
    re.compile(r"(?<![0-9A-Z])([1-4])Q[\s_\-.]*(\d{2})(?!\d)", re.I),  # 3Q24
    re.compile(r"(?<![0-9A-Z])Q([1-4])[\s_\-.]*(\d{2})(?!\d)", re.I),  # Q3-24
]
_FY = re.compile(r"(?<![A-Z])FY[\s_\-.]*((?:19|20)\d{2})(?!\d)", re.I)


@dataclass
class Identity:
    company_id: int | None
    period_end: date | None
    published: date | None
    doc_kind: str
    how: str


def period_from_name(name: str) -> date | None:
    """Calendar quarter end from a file name. Fiscal-year companies need a manifest."""
    for i, rx in enumerate(_Q_PATTERNS):
        m = rx.search(name)
        if not m:
            continue
        if i == 1:
            year, q = int(m.group(1)), int(m.group(2))
        else:
            q, y = int(m.group(1)), m.group(2)
            year = int(y) if len(y) == 4 else 2000 + int(y)
        mo, d = QUARTER_END[q]
        return date(year, mo, d)
    m = _FY.search(name)
    return date(int(m.group(1)), 12, 31) if m else None


def ticker_from_name(name: str, tickers: dict[str, int]) -> int | None:
    # folder names count too: archives are often laid out as <TICKER>/<year>/file.pdf
    tokens = {t.upper() for t in re.split(r"[^A-Za-z.]+", str(Path(name).with_suffix(""))) if t}
    hits = {tickers[t] for t in tokens if t in tickers and len(t) >= 2}
    return hits.pop() if len(hits) == 1 else None


def _manifest(path: Path | None) -> dict[str, dict[str, str]]:
    if not path:
        return {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return {r["path"].replace("\\", "/").strip(): r for r in csv.DictReader(fh)}


def identify(rel: str, tickers: dict[str, int], manifest: dict[str, dict[str, str]]) -> Identity:
    m = manifest.get(rel)
    if m:
        t = (m.get("ticker") or "").strip().upper()
        return Identity(
            tickers.get(t),
            date.fromisoformat(m["period_end"]) if m.get("period_end") else None,
            date.fromisoformat(m["published_date"]) if m.get("published_date") else None,
            (m.get("doc_kind") or "supplemental").strip(),
            "manifest",
        )
    return Identity(ticker_from_name(rel, tickers), period_from_name(rel), None, "supplemental", "filename")


def import_archive(
    db: Db, store: RawStore, folder: Path, *, run_id: int | None, manifest: Path | None = None
) -> dict[str, Any]:
    folder = Path(folder)
    man = _manifest(manifest)
    tickers: dict[str, int] = {}
    for r in db.query("SELECT company_id, ticker FROM ref_company_ticker_history"):
        tickers[r["ticker"].upper()] = r["company_id"]
    for r in db.query("SELECT company_id, ticker FROM ref_company WHERE ticker IS NOT NULL"):
        tickers[r["ticker"].upper()] = r["company_id"]
    stats = {"files": 0, "new": 0, "already_imported": 0, "duplicates": 0, "needs_review": 0}
    review: list[str] = []
    for p in sorted(folder.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in EXTS or p.name.startswith("."):
            continue
        stats["files"] += 1
        uri = p.resolve().as_uri()
        if db.scalar(
            "SELECT doc_id FROM stg_document WHERE source='manual_archive' AND source_url_hash=SHA2(:u,256)", {"u": uri}
        ):
            stats["already_imported"] += 1
            continue
        rel = p.relative_to(folder).as_posix()
        ident = identify(rel, tickers, man)
        sha, _ = store_local_file(
            db, store, p, source="manual_archive", run_id=run_id, content_type=EXTS[p.suffix.lower()]
        )
        doc_id = db.execute(
            """INSERT INTO stg_document (company_id, doc_kind, source, source_url, original_filename, mime_type,
                 period_end_date, published_date, registered_run_id)
               VALUES (:c, :k, 'manual_archive', :u, :fn, :mt, :pe, :pd, :run)""",
            {
                "c": ident.company_id,
                "k": ident.doc_kind,
                "u": uri,
                "fn": p.name[:255],
                "mt": EXTS[p.suffix.lower()],
                "pe": ident.period_end,
                "pd": ident.published,
                "run": run_id,
            },
        )
        status = mark_fetched(db, doc_id, sha, p.stat().st_size)
        if status == "duplicate":
            stats["duplicates"] += 1
        elif ident.company_id is None or ident.period_end is None:
            missing = " and ".join(
                x for x, v in (("company", ident.company_id), ("period", ident.period_end)) if v is None
            )
            db.execute(
                "UPDATE stg_document SET status='needs_review', status_note=:n WHERE doc_id=:d",
                {"d": doc_id, "n": f"could not identify {missing} from {ident.how}; add it to a manifest"},
            )
            stats["needs_review"] += 1
            review.append(rel)
        else:
            stats["new"] += 1
        db.commit()
    return {**stats, "review_files": review[:50]}
