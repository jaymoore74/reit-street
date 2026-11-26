"""Document parser framework (supplementals, earnings releases, presentations).

A parser is a pure function of the document bytes:

    class MyPdfParser:
        name = "pdf_tables"
        version = "0.1"
        def can_parse(self, doc: DocMeta) -> bool: ...
        def parse(self, data: bytes, doc: DocMeta) -> ParseResult: ...

Register it with register(MyPdfParser()). The `parse-docs` job feeds every fetched
document to the first parser that accepts it, stores the output in stg_doc_parse /
stg_doc_table / stg_doc_line_item with page and cell positions, and points the
document at that parse. Failures go to the dead-letter queue; a parse with no values
puts the document in needs_review. Old parses are kept, so a new parser version can be
compared against the previous one before it is trusted.

The real PDF parser is a separate piece of work (see docs/adr/0006); `manual_csv`
below lets an analyst key in values from any document today, and is the template.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Protocol

from ..core.errors import ParseError


@dataclass(frozen=True)
class DocMeta:
    doc_id: int
    company_id: int | None
    doc_kind: str
    source: str
    mime_type: str | None
    original_filename: str | None
    period_end_date: date | None


@dataclass
class ParsedTable:
    page_no: int
    table_index: int
    title: str | None = None
    section: str | None = None
    bbox: list[float] | None = None


@dataclass
class ParsedItem:
    page_no: int
    row_label: str
    value_numeric: Decimal | None
    raw_text: str | None = None
    table_index: int | None = None  # matches ParsedTable.table_index on the same page
    row_index: int | None = None
    col_index: int | None = None
    col_label: str | None = None
    scale_hint: str | None = None
    unit_hint: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    segment_label: str | None = None
    confidence: Decimal | None = None


@dataclass
class ParseResult:
    tables: list[ParsedTable] = field(default_factory=list)
    items: list[ParsedItem] = field(default_factory=list)


class DocumentParser(Protocol):
    name: str
    version: str

    def can_parse(self, doc: DocMeta) -> bool: ...
    def parse(self, data: bytes, doc: DocMeta) -> ParseResult: ...


_REGISTRY: list[DocumentParser] = []


def register(p: DocumentParser) -> None:
    _REGISTRY[:] = [x for x in _REGISTRY if x.name != p.name] + [p]


def parsers() -> list[DocumentParser]:
    return list(_REGISTRY)


def pick(doc: DocMeta) -> DocumentParser | None:
    return next((p for p in _REGISTRY if p.can_parse(doc)), None)


_NUM = re.compile(r"^\(?-?\$?\s*\(?([0-9][0-9,]*\.?[0-9]*)\)?\s*%?\)?$")


def parse_number(text: str | None) -> Decimal | None:
    """'(1,234.5)' -> -1234.5, '$ 12' -> 12, '4.5%' -> 4.5, dashes and blanks -> None."""
    if text is None:
        return None
    t = text.strip().replace("—", "").replace("–", "").replace("−", "-")
    if t in ("", "-", "--", "n/a", "N/A", "nm", "NM"):
        return None
    m = _NUM.match(t.replace(" ", ""))
    if not m:
        return None
    try:
        v = Decimal(m.group(1).replace(",", ""))
    except InvalidOperation:
        return None
    neg = t.startswith("(") or t.startswith("-") or "(" in t[:3]
    return -v if neg else v


class ManualCsvParser:
    """Analyst-keyed values. CSV columns: page_no,row_label,value,col_label,period_start,period_end,segment,
    unit_hint. Use for documents no automated parser handles yet, and to resolve QA items."""

    name = "manual_csv"
    version = "1.0"

    def can_parse(self, doc: DocMeta) -> bool:
        return (doc.mime_type or "").startswith("text/csv") or (doc.original_filename or "").lower().endswith(".csv")

    def parse(self, data: bytes, doc: DocMeta) -> ParseResult:
        try:
            rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
        except UnicodeDecodeError as e:
            raise ParseError(f"not UTF-8 CSV: {e}") from e
        if not rows or "row_label" not in rows[0] or "value" not in rows[0]:
            raise ParseError("manual CSV needs at least row_label and value columns")
        res = ParseResult(tables=[ParsedTable(page_no=1, table_index=1, title="manual entry")])
        for i, r in enumerate(rows, start=1):

            def d(k: str, r: dict[str, str] = r) -> date | None:
                return date.fromisoformat(r[k]) if r.get(k) else None

            res.items.append(
                ParsedItem(
                    page_no=int(r.get("page_no") or 1),
                    table_index=1,
                    row_index=i,
                    col_index=1,
                    row_label=r["row_label"].strip(),
                    col_label=r.get("col_label") or None,
                    raw_text=r["value"],
                    value_numeric=parse_number(r["value"]),
                    period_start=d("period_start"),
                    period_end=d("period_end"),
                    segment_label=r.get("segment") or None,
                    unit_hint=r.get("unit_hint") or None,
                    confidence=Decimal("1.000"),
                )
            )
        return res


register(ManualCsvParser())
