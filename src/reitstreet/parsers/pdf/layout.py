"""Page geometry to tables.

Supplementals are typeset documents, not tagged tables, and every REIT lays them out
differently. This module rebuilds tables from word positions only, so it does not care
which tool produced the PDF:

  words  -> lines (same baseline)
  lines  -> cells (words closer than a gap threshold merge; "$" joins its number)
  cells  -> blocks (runs of lines that carry numbers, allowing sub-heading lines)
  blocks -> columns (numbers cluster on their right edge, text on its left edge)
         -> header lines above the block, assigned to the columns they span
         -> title and scale note nearby

Everything keeps its bounding box, so each value can be traced to a spot on a page.
Pure functions; pdfplumber is only touched in `page_words`.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from itertools import pairwise
from statistics import median
from typing import Any

from ...core.errors import ParseError

YEAR_RX = re.compile(r"^(19[5-9]\d|20\d\d)$")
_NUMLIKE = re.compile(r"^[\(\-−–—$€£]*\s*[\d.,]+\s*[%x)]*\)?$|^[—–\-]+$|^n/?a$|^nm$", re.I)
_PCT_OR_MULT = re.compile(r"[%x]\)?$", re.I)


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    size: float
    bold: bool


@dataclass
class Cell:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    size: float
    bold: bool

    @property
    def numeric(self) -> bool:
        t = self.text.replace(" ", "")
        return (bool(_NUMLIKE.match(t)) and any(ch.isdigit() for ch in t)) or t in ("—", "–", "-", "--")

    @property
    def is_year(self) -> bool:
        return bool(YEAR_RX.match(self.text.strip()))

    @property
    def bbox(self) -> list[float]:
        return [round(self.x0, 1), round(self.top, 1), round(self.x1, 1), round(self.bottom, 1)]


@dataclass
class Line:
    cells: list[Cell]
    top: float
    bottom: float

    @property
    def text(self) -> str:
        return " ".join(c.text for c in self.cells)

    @property
    def n_numeric(self) -> int:
        return sum(1 for c in self.cells if c.numeric)

    @property
    def size(self) -> float:
        return max(c.size for c in self.cells)

    @property
    def bold(self) -> bool:
        return all(c.bold for c in self.cells)


@dataclass
class Column:
    index: int  # 1-based, label column excluded
    x0: float
    x1: float
    align: str  # 'R' numbers, 'L' text
    header: str = ""
    support: int = 0


@dataclass
class TableRow:
    index: int  # 1-based within the table
    label: str
    label_cell: Cell | None
    cells: dict[int, Cell]  # column index -> cell
    top: float
    bottom: float
    group: str | None = None  # nearest sub-heading line above it inside the table
    is_total: bool = False
    ambiguous_cols: set[int] = field(default_factory=set)


@dataclass
class Table:
    page_no: int
    index: int
    title: str | None
    title_lines: list[str]
    columns: list[Column]
    rows: list[TableRow]
    header_lines: list[str]
    bbox: list[float]
    page_heading: str | None
    scale_text: str | None
    context_text: str  # title + headers + nearby notes, for classification


# ---------------------------------------------------------------- extraction
def page_words(data: bytes, max_pages: int = 400) -> Iterator[tuple[int, float, list[Word], str]]:
    """(page_no, page_width, words, page_text) for each page. Raises ParseError on unreadable PDFs."""
    import pdfplumber  # local import: heavy, and only parsers need it

    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as e:  # pdfminer raises many types for bad files
        raise ParseError(f"not a readable PDF: {type(e).__name__}: {e}") from e
    with pdf:
        if len(pdf.pages) > max_pages:
            raise ParseError(f"{len(pdf.pages)} pages exceeds max_pages={max_pages}")
        for i, page in enumerate(pdf.pages, start=1):
            try:
                raw = page.extract_words(
                    x_tolerance=1.5, y_tolerance=2.0, keep_blank_chars=False, extra_attrs=["size", "fontname"]
                )
            except Exception as e:
                raise ParseError(f"page {i}: text extraction failed: {type(e).__name__}: {e}") from e
            words = [
                Word(
                    text=w["text"],
                    x0=float(w["x0"]),
                    x1=float(w["x1"]),
                    top=float(w["top"]),
                    bottom=float(w["bottom"]),
                    size=float(w.get("size") or 8.0),
                    bold=bool(re.search(r"bold|black|heavy|semibold|demi", str(w.get("fontname", "")), re.I)),
                )
                for w in raw
                if str(w["text"]).strip()
            ]
            yield i, float(page.width), words, " ".join(w.text for w in words)


# ---------------------------------------------------------------- lines and cells
def build_lines(words: list[Word]) -> list[Line]:
    words = sorted(words, key=lambda w: (round(w.top, 0), w.x0))
    rows: list[list[Word]] = []
    for w in sorted(words, key=lambda w: w.top):
        tol = max(1.5, 0.3 * w.size)
        for r in rows:
            if abs(r[0].top - w.top) <= tol or abs(r[0].bottom - w.bottom) <= tol:
                r.append(w)
                break
        else:
            rows.append([w])
    lines = []
    for r in rows:
        r.sort(key=lambda w: w.x0)
        lines.append(Line(cells=_cells(r), top=min(w.top for w in r), bottom=max(w.bottom for w in r)))
    lines.sort(key=lambda ln: ln.top)
    return lines


def _cells(words: list[Word]) -> list[Cell]:
    cells: list[Cell] = []
    for w in words:
        if cells:
            c = cells[-1]
            gap = w.x0 - c.x1
            joiner = c.text in ("$", "(", "($", "$(", "€", "£") or w.text in (")", "%", ")%")
            same_kind = not (_is_num(c.text) and _is_num(w.text))
            if (gap <= max(2.0, 0.55 * w.size) and (same_kind or joiner)) or (joiner and gap <= 2.5 * w.size):
                c.text = f"{c.text}{'' if joiner else ' '}{w.text}" if not joiner else _join(c.text, w.text)
                c.x1, c.top, c.bottom = w.x1, min(c.top, w.top), max(c.bottom, w.bottom)
                c.bold = c.bold and w.bold
                continue
        cells.append(Cell(w.text, w.x0, w.x1, w.top, w.bottom, w.size, w.bold))
    return cells


def _join(a: str, b: str) -> str:
    return f"{a} {b}" if a in ("$", "€", "£") else f"{a}{b}"


def _is_num(t: str) -> bool:
    t = t.replace(" ", "")
    return bool(_NUMLIKE.match(t)) and any(ch.isdigit() for ch in t)


# ---------------------------------------------------------------- blocks
def _is_data_line(ln: Line) -> bool:
    nums = [c for c in ln.cells if c.numeric]
    if not nums:
        return False
    # a line of only years with no label is a header, not data
    first = ln.cells[0]
    return not (all(c.is_year for c in nums) and (first.numeric or len(nums) >= 2) and not _has_label(ln))


def _has_label(ln: Line) -> bool:
    return not ln.cells[0].numeric or (ln.cells[0].is_year and len(ln.cells) > 1 and not ln.cells[1].is_year)


def find_blocks(lines: list[Line]) -> list[tuple[int, int]]:
    """Index ranges [start, end) of lines forming tables: data lines, with up to two
    text-only lines (sub-headings) between them, and no large vertical gap."""
    pitches = [b.top - a.top for a, b in pairwise(lines) if 0 < b.top - a.top < 40]
    pitch = median(pitches) if pitches else 12.0
    blocks: list[tuple[int, int]] = []
    i = 0
    n = len(lines)
    while i < n:
        if not _is_data_line(lines[i]):
            i += 1
            continue
        start = end = i
        j = i + 1
        gap_text = 0
        while j < n:
            vgap = lines[j].top - lines[j - 1].top
            if vgap > max(3.2 * pitch, 30):
                break
            if _is_data_line(lines[j]):
                end = j
                gap_text = 0
            else:
                gap_text += 1
                if gap_text > 2 or len(lines[j].text) > 140:
                    break
            j += 1
        if end - start + 1 >= 2:
            blocks.append((start, end + 1))
        i = end + 1
    return blocks


# ---------------------------------------------------------------- columns
def _cluster(values: list[float], tol: float) -> list[list[float]]:
    out: list[list[float]] = []
    for v in sorted(values):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return out


def build_table(
    page_no: int, index: int, lines: list[Line], span: tuple[int, int], page_heading: str | None, page_text: str
) -> Table | None:
    body = lines[span[0] : span[1]]
    data_lines = [ln for ln in body if _is_data_line(ln)]
    # label region: text at the left of each data line, up to the first numeric cell
    label_x0 = min(ln.cells[0].x0 for ln in data_lines)
    # a year (or other number) printed flush left with the text labels is a row label: "2027", "Thereafter"
    text_first = [ln.cells[0] for ln in data_lines if not ln.cells[0].numeric]
    label_nums = {
        id(ln.cells[0])
        for ln in data_lines
        if ln.cells[0].numeric
        and len(ln.cells) > 1
        and abs(ln.cells[0].x0 - label_x0) <= 4
        and (ln.cells[0].is_year or any(abs(c.x0 - ln.cells[0].x0) <= 4 for c in text_first))
    }
    num_cells = [c for ln in data_lines for c in ln.cells if c.numeric and id(c) not in label_nums]
    if not num_cells:
        return None
    first_num_x0 = min(c.x0 for c in num_cells)
    # text columns to the right of the label (city, state, property type ...) sit between labels and numbers
    text_cells_right = [
        c
        for ln in data_lines
        for k, c in enumerate(ln.cells)
        if not c.numeric and k > 0 and c.x0 > label_x0 + 40 and c.x0 < max(cc.x1 for cc in num_cells)
    ]
    cols: list[Column] = []
    for grp in _cluster([c.x1 for c in num_cells], 6.0):
        cols.append(Column(0, x0=min(grp), x1=max(grp), align="R", support=len(grp)))
    for grp in _cluster([c.x0 for c in text_cells_right], 8.0):
        if len(grp) >= max(2, len(data_lines) // 3):
            cols.append(Column(0, x0=min(grp), x1=max(grp), align="L", support=len(grp)))
    cols.sort(key=lambda c: c.x0 if c.align == "L" else c.x1)
    for k, c in enumerate(cols, start=1):
        c.index = k
    label_right = min(
        [c.x0 - 1 for c in cols if c.align == "L"] + [first_num_x0 - 1],
    )

    def assign(cell: Cell) -> tuple[int | None, bool]:
        """Column for a cell, and whether the choice was ambiguous."""
        best: list[tuple[float, int]] = []
        for c in cols:
            key = cell.x1 if c.align == "R" else cell.x0
            if c.align == "R" and not cell.numeric and cell.x0 < label_right:
                continue
            ref = (c.x0 + c.x1) / 2
            best.append((abs(key - ref), c.index))
        if not best:
            return None, False
        best.sort()
        ambiguous = len(best) > 1 and best[1][0] - best[0][0] < 3.0
        return (best[0][1] if best[0][0] <= 14.0 else None), ambiguous

    rows: list[TableRow] = []
    group: str | None = None
    for ln in body:
        if not _is_data_line(ln):
            txt = ln.text.strip()
            group = txt.rstrip(":") if len(txt) <= 80 else group
            continue
        label_parts = [
            c
            for c in ln.cells
            if id(c) in label_nums or (c.x1 <= label_right + 2 and not (c.numeric and c.x0 > first_num_x0 - 2))
        ]
        label_cell = None
        if label_parts:
            label_cell = Cell(
                " ".join(c.text for c in label_parts),
                label_parts[0].x0,
                label_parts[-1].x1,
                min(c.top for c in label_parts),
                max(c.bottom for c in label_parts),
                label_parts[0].size,
                all(c.bold for c in label_parts),
            )
        row = TableRow(
            index=len(rows) + 1,
            label=label_cell.text.strip() if label_cell else "",
            label_cell=label_cell,
            cells={},
            top=ln.top,
            bottom=ln.bottom,
            group=group,
        )
        for cell in ln.cells:
            if cell in label_parts:
                continue
            col, amb = assign(cell)
            if col is None:
                continue
            if col in row.cells:  # two cells in one column: keep both texts, flag it
                prev = row.cells[col]
                row.cells[col] = Cell(
                    f"{prev.text} {cell.text}", prev.x0, cell.x1, prev.top, cell.bottom, cell.size, False
                )
                amb = True
            else:
                row.cells[col] = cell
            if amb:
                row.ambiguous_cols.add(col)
        row.is_total = bool(re.match(r"^(total|subtotal|sub-total|grand total|consolidated total)\b", row.label, re.I))
        rows.append(row)
    if not rows or not cols:
        return None

    # header lines: up to 4 non-data lines right above the block, aligned to columns
    body_size = median([c.size for ln in data_lines for c in ln.cells])
    header_lines: list[Line] = []
    k = span[0] - 1
    top_limit = body[0].top
    while k >= 0 and len(header_lines) < 4:
        ln = lines[k]
        if _is_data_line(ln) or top_limit - ln.bottom > 3.5 * max(ln.size, 6):
            break
        if len(ln.text) > 160 or ln.size > 1.2 * body_size:
            break  # long prose or a larger font: a title or note, not a column header
        if not any(x.x1 > label_right + 2 for x in ln.cells) and not any(x.is_year for x in ln.cells):
            break  # nothing over the value columns: stub note such as "(dollars in thousands)"
        header_lines.insert(0, ln)
        top_limit = ln.top
        k -= 1
    for ln in header_lines:
        for h in ln.cells:
            if h.x1 <= label_right and not h.is_year:
                continue  # stub header such as "(in thousands)" or "Property"
            spanned = [hc for hc in cols if h.x0 - 6 <= (hc.x0 + hc.x1) / 2 <= h.x1 + 6]
            if not spanned:
                spanned = [min(cols, key=lambda hc: abs((hc.x0 + hc.x1) / 2 - (h.x0 + h.x1) / 2))]
                if abs((spanned[0].x0 + spanned[0].x1) / 2 - (h.x0 + h.x1) / 2) > 40:
                    continue
            for hc in spanned:
                hc.header = f"{hc.header} {h.text}".strip()

    # title: closest short line above the headers (bold or larger preferred)
    title_lines: list[str] = []
    k2 = k
    looked = 0
    title_top = header_lines[0].top if header_lines else body[0].top
    while k2 >= 0 and looked < 4:
        ln = lines[k2]
        if title_top - ln.bottom > 60:
            break
        if _is_data_line(ln):
            break
        title_lines.insert(0, ln.text.strip())
        title_top = ln.top
        looked += 1
        k2 -= 1
    title = _pick_title(lines[max(0, k2 + 1) : k + 1] if k >= 0 else [], header_lines)
    stub = " ".join(c.text for ln in header_lines for c in ln.cells if c.x1 <= label_right)
    context = " ".join([*title_lines, " ".join(ln.text for ln in header_lines), stub])
    scale_src = context if SCALE_RX.search(context) else (page_text if SCALE_RX.search(page_text) else "")
    m = SCALE_RX.search(scale_src)
    all_cells = [c for r in rows for c in r.cells.values()] + [r.label_cell for r in rows if r.label_cell]
    bbox = [
        round(min(c.x0 for c in all_cells), 1),
        round(min(c.top for c in all_cells), 1),
        round(max(c.x1 for c in all_cells), 1),
        round(max(c.bottom for c in all_cells), 1),
    ]
    return Table(
        page_no=page_no,
        index=index,
        title=title,
        title_lines=title_lines,
        columns=cols,
        rows=rows,
        header_lines=[ln.text for ln in header_lines],
        bbox=bbox,
        page_heading=page_heading,
        scale_text=m.group(0) if m else None,
        context_text=context,
    )


SCALE_RX = re.compile(
    r"(in|\$|dollars in|amounts in|\(\$?\s*in)\s*(thousands|millions|billions)|\$\s*\(?0{3}s?\)?|\(000'?s\)|"
    r"\$\s*(thousands|millions|billions)|(thousands|millions) of dollars|\$\s*in\s*(mm|m)\b",
    re.I,
)


def _pick_title(cands: list[Line], header_lines: list[Line]) -> str | None:
    if not cands:
        return None
    scored = sorted(
        cands,
        key=lambda ln: (ln.bold or ln.size >= 9.5, ln.size, ln.top),
        reverse=True,
    )
    best = scored[0]
    t = best.text.strip()
    return t[:512] if t else None


def page_heading(lines: list[Line]) -> str | None:
    """Largest text in the top fifth of the page: most supplementals print a section name there."""
    if not lines:
        return None
    top_zone = [ln for ln in lines if ln.top <= lines[0].top + 120 and ln.n_numeric == 0]
    if not top_zone:
        return None
    best = max(top_zone, key=lambda ln: (ln.size, ln.bold, -ln.top))
    return best.text.strip()[:255] or None


def tables_on_page(page_no: int, words: list[Word], page_text: str) -> list[Table]:
    lines = build_lines(words)
    heading = page_heading(lines)
    out: list[Table] = []
    for span in find_blocks(lines):
        t = build_table(page_no, len(out) + 1, lines, span, heading, page_text)
        if t is not None:
            out.append(t)
    return out


def describe(t: Table) -> dict[str, Any]:
    """Small dict for debugging and parse-eval output."""
    return {
        "page": t.page_no,
        "index": t.index,
        "title": t.title,
        "heading": t.page_heading,
        "scale": t.scale_text,
        "columns": [c.header for c in t.columns],
        "rows": len(t.rows),
    }
