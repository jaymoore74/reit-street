"""Parse data.sec.gov submissions JSON (main file and paged history files)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from ..core.errors import ParseError

PARSER_VERSION = "sub-1.0"

_COLS = (
    "accessionNumber",
    "filingDate",
    "reportDate",
    "acceptanceDateTime",
    "act",
    "form",
    "fileNumber",
    "filmNumber",
    "items",
    "size",
    "isXBRL",
    "isInlineXBRL",
    "primaryDocument",
    "primaryDocDescription",
)


def _d(s: Any) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _dt(s: Any) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


@dataclass
class Submissions:
    name: str | None = None
    sic: int | None = None
    sic_description: str | None = None
    entity_type: str | None = None
    fiscal_year_end: str | None = None
    state_of_incorporation: str | None = None
    tickers: list[tuple[str, str | None]] = field(default_factory=list)
    former_names: list[tuple[str, date | None, date | None]] = field(default_factory=list)
    filings: list[dict[str, Any]] = field(default_factory=list)
    page_files: list[str] = field(default_factory=list)


def _filings(block: dict[str, Any]) -> list[dict[str, Any]]:
    accs = block.get("accessionNumber") or []
    cols = {c: (block.get(c) or []) for c in _COLS}
    out = []
    for i, acc in enumerate(accs):

        def g(c: str, i: int = i) -> Any:
            v = cols[c]
            return v[i] if i < len(v) else None

        if not acc or not g("form") or not _d(g("filingDate")):
            continue
        out.append(
            {
                "accession_no": acc,
                "form_type": g("form"),
                "filing_date": _d(g("filingDate")),
                "report_date": _d(g("reportDate")),
                "acceptance_datetime": _dt(g("acceptanceDateTime")),
                "act": g("act") or None,
                "file_number": g("fileNumber") or None,
                "film_number": g("filmNumber") or None,
                "items": g("items") or None,
                "size_bytes": g("size"),
                "is_xbrl": g("isXBRL"),
                "is_inline_xbrl": g("isInlineXBRL"),
                "primary_document": g("primaryDocument") or None,
                "primary_doc_description": g("primaryDocDescription") or None,
            }
        )
    return out


def parse_submissions(body: bytes) -> Submissions:
    """Main CIK##########.json file."""
    try:
        sub = json.loads(body)
        recent = sub["filings"]["recent"]
    except (ValueError, KeyError, TypeError) as e:
        raise ParseError(f"not a submissions document: {e}") from e
    sic = str(sub.get("sic") or "")
    exch = sub.get("exchanges") or []
    return Submissions(
        name=sub.get("name"),
        sic=int(sic) if sic.isdigit() else None,
        sic_description=sub.get("sicDescription"),
        entity_type=sub.get("entityType"),
        fiscal_year_end=sub.get("fiscalYearEnd") or None,
        state_of_incorporation=sub.get("stateOfIncorporation") or None,
        tickers=[(t.upper(), exch[i] if i < len(exch) else None) for i, t in enumerate(sub.get("tickers") or [])],
        former_names=[
            (f.get("name"), _d(f.get("from")), _d(f.get("to"))) for f in sub.get("formerNames") or [] if f.get("name")
        ],
        filings=_filings(recent),
        page_files=[f["name"] for f in sub["filings"].get("files") or [] if f.get("name")],
    )


def parse_submissions_page(body: bytes) -> list[dict[str, Any]]:
    """Older paged file, e.g. CIK0001045609-submissions-001.json (columns at top level)."""
    try:
        block = json.loads(body)
    except ValueError as e:
        raise ParseError(f"bad submissions page: {e}") from e
    if not isinstance(block, dict) or "accessionNumber" not in block:
        raise ParseError("submissions page has no accessionNumber column")
    return _filings(block)
