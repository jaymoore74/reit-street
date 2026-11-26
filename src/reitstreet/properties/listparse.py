"""Read a property list (CSV, JSON, HTML table, XLSX) into rows, and map columns to
PropertyRecord fields. Pure stdlib, so it runs anywhere and is unit tested offline.

A field map is JSON {field: column}. Columns are matched case-insensitively on the
header text (or a dotted key path for JSON). Without a field map, headers are guessed
from SYNONYMS; the guess is reported so a person can pin it in property_sources.csv.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any

from ..core import xmlsafe

SYNONYMS: dict[str, tuple[str, ...]] = {
    "name": (
        "property name",
        "property",
        "name",
        "community",
        "center",
        "hotel",
        "facility",
        "building",
        "asset",
        "location name",
    ),
    "address_line": ("address", "street address", "street", "address 1", "address line 1", "property address"),
    "city": ("city", "town"),
    "state": ("state", "st", "state/province", "province"),
    "postal_code": ("zip", "zip code", "postal code", "zipcode", "postal"),
    "market": ("market", "msa", "metro", "submarket", "region", "metropolitan area"),
    "raw_property_type": ("property type", "type", "asset type", "segment", "product type", "sector", "use"),
    "rentable_sf": (
        "square feet",
        "sq ft",
        "sf",
        "rentable square feet",
        "rsf",
        "gla",
        "nra",
        "gross leasable area",
        "rentable sf",
    ),
    "unit_count": (
        "units",
        "unit count",
        "homes",
        "apartment units",
        "beds",
        "keys",
        "rooms",
        "sites",
        "licensed beds",
        "operating beds",
    ),
    "year_built": ("year built", "built", "year completed", "year constructed"),
    "acquired_text": ("acquired", "date acquired", "acquisition date", "year acquired"),
    "occupancy_pct": ("occupancy", "% leased", "leased %", "occupied", "% occupied", "percent leased", "occupancy %"),
    "ownership_pct": ("ownership", "ownership %", "% owned", "our share", "ownership interest", "economic interest"),
    "source_property_ref": (
        "property id",
        "id",
        "property code",
        "store number",
        "store #",
        "site id",
        "facility id",
        "ccn",
    ),
    "latitude": ("latitude", "lat"),
    "longitude": ("longitude", "lng", "lon", "long"),
    "tenant": ("tenant", "operator", "primary tenant", "anchor"),
}
NUMERIC = {"rentable_sf", "unit_count", "occupancy_pct", "ownership_pct", "latitude", "longitude"}


# ---------------------------------------------------------------------- readers
class _Tables(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._stack: list[list[list[str]]] = []
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._stack[-1].append([])
        elif tag in ("td", "th") and self._stack:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None and self._stack:
            if not self._stack[-1]:
                self._stack[-1].append([])
            self._stack[-1][-1].append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "table" and self._stack:
            t = [r for r in self._stack.pop() if any(c for c in r)]
            if t:
                self.tables.append(t)

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def html_tables(html: str) -> list[list[list[str]]]:
    p = _Tables()
    p.feed(html)
    return p.tables


def _rows_from_grid(grid: list[list[str]]) -> list[dict[str, str]]:
    # header = first row with >= 2 non-empty cells that look like labels
    for i, row in enumerate(grid[:10]):
        if sum(1 for c in row if c and not _is_number(c)) >= 2:
            hdr = [c.strip() for c in row]
            out = []
            for r in grid[i + 1 :]:
                if not any(c.strip() for c in r):
                    continue
                out.append(
                    {
                        hdr[j] if j < len(hdr) and hdr[j] else f"col{j}": (r[j] if j < len(r) else "")
                        for j in range(max(len(hdr), len(r)))
                    }
                )
            return out
    return []


def xlsx_grid(body: bytes, sheet: str | None = None) -> list[list[str]]:
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rel = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in xmlsafe.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t")))
        wb = xmlsafe.fromstring(z.read("xl/workbook.xml"))
        rels = xmlsafe.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        targets = {r.get("Id"): r.get("Target", "") for r in rels}
        node = wb.find("m:sheets", ns)
        sheets = [(s.get("name"), targets.get(s.get(rel))) for s in (node if node is not None else [])]
        chosen = next(
            (t for n, t in sheets if sheet and n and n.lower() == sheet.lower()), sheets[0][1] if sheets else None
        )
        if not chosen:
            return []
        path = chosen.lstrip("/") if chosen.startswith("/") else "xl/" + chosen
        root = xmlsafe.fromstring(z.read(path))
    grid: list[list[str]] = []
    for row in root.iter(f"{{{ns['m']}}}row"):
        cells: dict[int, str] = {}
        for c in row.findall("m:c", ns):
            ref = c.get("r", "")
            col = 0
            letters = re.match(r"[A-Z]+", ref)
            for ch in letters.group(0) if letters else "":
                col = col * 26 + (ord(ch) - 64)
            v = c.find("m:v", ns)
            t = c.get("t")
            if t == "s" and v is not None and v.text is not None:
                val = shared[int(v.text)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{{{ns['m']}}}t"))
            else:
                val = v.text if v is not None and v.text is not None else ""
            cells[max(col - 1, 0)] = val
        if cells:
            grid.append([cells.get(i, "") for i in range(max(cells) + 1)])
    return grid


def _dig(obj: Any, path: str | None) -> Any:
    for part in (path or "").split("."):
        if not part:
            continue
        obj = obj[int(part)] if isinstance(obj, list) else obj.get(part) if isinstance(obj, dict) else None
    return obj


def _flatten(d: dict[str, Any], prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        elif isinstance(v, list):
            out[key] = ", ".join(str(x) for x in v if not isinstance(x, dict | list))
        elif v is not None:
            out[key] = str(v)
    return out


def read_rows(body: bytes, fmt: str, records_path: str | None = None) -> list[dict[str, str]]:
    """records_path: JSON dotted path to the list; HTML table index (default: largest
    table); XLSX sheet name."""
    if fmt == "csv":
        text = body.decode("utf-8-sig", errors="replace")
        return [dict(r) for r in csv.DictReader(io.StringIO(text))]
    if fmt == "json":
        data = _dig(json.loads(body), records_path)
        return [_flatten(x) for x in (data or []) if isinstance(x, dict)]
    if fmt in ("html_table", "html"):
        tables = html_tables(body.decode("utf-8", errors="replace"))
        if not tables:
            return []
        grid = tables[int(records_path)] if records_path and records_path.isdigit() else max(tables, key=len)
        return _rows_from_grid(grid)
    if fmt == "xlsx":
        return _rows_from_grid(xlsx_grid(body, records_path))
    raise ValueError(f"unsupported list format {fmt!r}")


# ---------------------------------------------------------------------- mapping
def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9%#/]+", " ", s.lower()).strip()


def guess_field_map(headers: Iterable[str]) -> dict[str, str]:
    hs = list(headers)
    keyed = {_key(h): h for h in hs}
    out: dict[str, str] = {}
    used: set[str] = set()
    for field, syns in SYNONYMS.items():
        for s in syns:
            h = keyed.get(_key(s))
            if h and h not in used:
                out[field] = h
                used.add(h)
                break
    return out


def _is_number(s: str) -> bool:
    return to_decimal(s) is not None


def to_decimal(s: Any) -> Decimal | None:
    if s is None:
        return None
    t = str(s).strip().replace(",", "").replace("$", "").replace("%", "")
    if t in ("", "-", "n/a", "N/A", "—"):
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    try:
        v = Decimal(t)
    except InvalidOperation:
        return None
    return -v if neg else v


def map_rows(
    rows: list[dict[str, str]], field_map: dict[str, str] | None
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Returns (mapped rows, field map used). Unmapped columns go to attributes."""
    if not rows:
        return [], field_map or {}
    fm = field_map or guess_field_map(rows[0].keys())
    lower = {k.lower(): k for k in rows[0]}
    cols = {f: lower.get(c.lower(), c) for f, c in fm.items()}
    out = []
    for r in rows:
        m: dict[str, Any] = {}
        for f, c in cols.items():
            v = (r.get(c) or "").strip()
            if not v:
                continue
            if f in NUMERIC:
                d = to_decimal(v)
                if d is not None:
                    if f in ("occupancy_pct", "ownership_pct") and d <= 1 and "%" not in v:
                        d = d * 100
                    m[f] = d
            else:
                m[f] = v
        extra = {k: v for k, v in r.items() if k not in cols.values() and v not in (None, "")}
        if "tenant" in m:
            extra["tenant"] = m.pop("tenant")
        m["attributes"] = extra
        if any(m.get(k) for k in ("name", "address_line", "source_property_ref")):
            out.append(m)
    return out, fm
