"""Parse EDGAR filing index pages (<accession>-index.htm), 'Document Format Files' table,
and classify 8-K exhibits (where many REITs file their supplemental)."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

PARSER_VERSION = "idx-1.0"

SUPP_DESC_RX = re.compile(
    r"supplement|operating and financial|financial (and|&) operating|"
    r"fact ?book|investor package|quarterly (financial )?(package|data)",
    re.I,
)
RELEASE_DESC_RX = re.compile(r"press release|earnings release|news release|results", re.I)
PRESENTATION_RX = re.compile(r"presentation|slides|investor deck", re.I)


class _IndexParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_table = False
        self.rows: list[list[tuple[str, str | None]]] = []
        self._row: list[tuple[str, str | None]] | None = None
        self._cell: list[str] | None = None
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "table" and "tablefile" in (a.get("class") or "").lower():
            self.in_table = True
        elif self.in_table and tag == "tr":
            self._row = []
        elif self.in_table and tag in ("td", "th") and self._row is not None:
            self._cell, self._href = [], None
        elif self.in_table and tag == "a" and self._cell is not None:
            self._href = a.get("href")

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self.in_table:
            self.in_table = False
        elif self.in_table and tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(("".join(self._cell).strip(), self._href))
            self._cell = None
        elif self.in_table and tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def parse_index_html(html: str) -> list[dict[str, Any]]:
    p = _IndexParser()
    p.feed(html)
    docs = []
    for row in p.rows:
        if len(row) < 4 or row[0][0].lower() == "seq":
            continue
        seq, desc, doc, dtype = row[0][0], row[1][0], row[2], row[3][0]
        size = row[4][0] if len(row) > 4 else ""
        name = (doc[0].split() or [""])[0]
        if not name:
            continue
        docs.append(
            {
                "seq": int(seq) if seq.isdigit() else None,
                "description": desc or None,
                "document_name": name[:255],
                "doc_type": dtype or None,
                "size_bytes": int(size) if size.isdigit() else None,
            }
        )
    return docs


def wants_index(form_type: str | None, items: str | None) -> bool:
    """Index pages worth fetching: periodic reports and earnings / Reg FD 8-Ks."""
    form = (form_type or "").upper()
    if form in ("10-K", "10-Q", "10-K/A", "10-Q/A"):
        return True
    return form.startswith("8-K") and any(i in (items or "") for i in ("2.02", "7.01"))


def classify_exhibit(doc: dict[str, Any]) -> str | None:
    """Return a stg_document.doc_kind for an 8-K EX-99 exhibit, or None if not an EX-99."""
    dtype = (doc.get("doc_type") or "").upper()
    if not dtype.startswith("EX-99"):
        return None
    desc = doc.get("description") or ""
    name = doc.get("document_name") or ""
    if SUPP_DESC_RX.search(desc) or SUPP_DESC_RX.search(name):
        return "supplemental"
    if PRESENTATION_RX.search(desc):
        return "presentation"
    if RELEASE_DESC_RX.search(desc) or dtype == "EX-99.1":
        return "earnings_release"
    return "exhibit_other"


def mime_for(name: str) -> str:
    n = name.lower()
    if n.endswith(".pdf"):
        return "application/pdf"
    if n.endswith((".htm", ".html")):
        return "text/html"
    if n.endswith(".xlsx"):
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if n.endswith(".xls"):
        return "application/vnd.ms-excel"
    return "application/octet-stream"
