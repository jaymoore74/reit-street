"""Write a ``Book`` to .xlsx in the house style.

House style (from Jason's reference models, see models/reference-notes.md):
Arial 8 everywhere and nothing larger; inputs and assumptions bright blue, formulas black, formulas that read
another sheet green; gridlines off; row 1 ticker, row 2 "TICKER: Sheet title", row 3 a dark header bar with the
unit note and period labels; dark section bars; light-blue fill on key totals; double underline on grand totals.
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils.indexed_list import IndexedList
from openpyxl.worksheet.worksheet import Worksheet

from .workbook import FIRST_DATA_COL, HEADER_ROWS, Address, Book, Cell, Row, col_letter, sheet_ref

FONT_NAME = "Arial"
FONT_SIZE = 8
ROW_HEIGHT = 11.25
BLUE = "FF0000FF"
BLACK = "FF000000"
GREEN = "FF008000"
WHITE = "FFFFFFFF"
BAR = "FF333F4F"
TOTAL_FILL = "FFD6DCE4"

NUMBER_FORMATS: dict[str, str] = {
    "num": "#,##0_);(#,##0)",
    "dollar": '"$"#,##0_);\\("$"#,##0\\)',
    "ps": '"$"#,##0.00_);\\("$"#,##0.00\\)',
    "pct": "0.00%",
    "pct1": "0.0%",
    "bp": '0" bp"',
    "mult": '0.0"x"',
    "dec1": "#,##0.0_);(#,##0.0)",
    "dec3": "0.000",
    "int": "0",
    "text": "@",
}

_THIN = Side(style="thin")
_DOUBLE = Side(style="double")


def _font(color: str = BLACK, bold: bool = False) -> Font:
    return Font(name=FONT_NAME, size=FONT_SIZE, color=color, bold=bold)


def write_xlsx(book: Book, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    idx = book.index()
    wb = Workbook()
    _set_default_font(wb)
    wb.remove(wb.active)
    for sh in book.sheets:
        ws = wb.create_sheet(sh.name)
        ncols = max([len(sh.headers)] + [len(r.cells) for r in sh.rows] + [1])
        last_col = FIRST_DATA_COL + ncols - 1
        _header(ws, book, sh.title, sh.unit_note, sh.headers, last_col)
        for i, row in enumerate(sh.rows):
            r = HEADER_ROWS + 1 + i
            _row(ws, book, idx, sh.name, r, row, last_col)
        ws.column_dimensions["A"].width = sh.label_width
        for j in range(ncols):
            w = sh.col_widths[j] if j < len(sh.col_widths) else 11.0
            ws.column_dimensions[col_letter(FIRST_DATA_COL + j)].width = w
        for r in range(1, HEADER_ROWS + len(sh.rows) + 1):
            ws.row_dimensions[r].height = ROW_HEIGHT
        ws.sheet_format.defaultRowHeight = ROW_HEIGHT
        ws.sheet_format.customHeight = True
        ws.sheet_view.showGridLines = False
        ws.freeze_panes = ws.cell(row=HEADER_ROWS + 1, column=FIRST_DATA_COL)
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
    wb.properties.title = f"{book.ticker} NAV and AFFO model"
    wb.properties.creator = "reitstreet"
    wb.save(path)
    return path


def _set_default_font(wb: Workbook) -> None:
    """Make Arial 8 the workbook's Normal style, so even empty cells and row heights follow it."""
    font = _font()
    wb._fonts = IndexedList([font])  # font 0 is the one the Normal style uses
    for style in wb._named_styles:
        if style.name == "Normal":
            style.font = font


def _header(ws: Worksheet, book: Book, title: str, unit_note: str, headers: list[str], last_col: int) -> None:
    ws.cell(row=1, column=1, value=book.ticker).font = _font(bold=True)
    ws.cell(row=2, column=1, value=f"{book.ticker}: {title}").font = _font(bold=True)
    fill = PatternFill("solid", fgColor=BAR)
    for col in range(1, last_col + 1):
        c = ws.cell(row=3, column=col)
        c.fill = fill
        c.font = _font(WHITE, bold=True)
        if col > 1:
            c.alignment = Alignment(horizontal="right")
    ws.cell(row=3, column=1, value=unit_note)
    for j, h in enumerate(headers):
        ws.cell(row=3, column=FIRST_DATA_COL + j, value=h)


def _row(
    ws: Worksheet, book: Book, idx: dict[str, tuple[Address, Cell]], sheet: str, r: int, row: Row, last: int
) -> None:
    label = ws.cell(row=r, column=1, value=row.label or None)
    bold = row.style in ("section", "total", "grand", "bold", "colhead")
    label.font = _font(WHITE if row.style == "section" else BLACK, bold=bold)
    if row.indent:
        label.alignment = Alignment(indent=row.indent)
    for j, c in enumerate(row.cells):
        if c is None:
            continue
        cell = ws.cell(row=r, column=FIRST_DATA_COL + j)
        if c.kind == "calc":
            assert c.formula is not None
            cross = False

            def ref(key: str) -> str:
                nonlocal cross
                if key in book.groups:
                    sh, col, r0, r1 = book.group_range(key, idx)
                    rng = f"{col_letter(col)}{r0}:{col_letter(col)}{r1}"
                    if sh != sheet:
                        cross = True
                        return f"{sheet_ref(sh)}!{rng}"
                    return rng
                addr = idx[key][0]
                if addr.sheet != sheet:
                    cross = True
                    return f"{sheet_ref(addr.sheet)}!{addr.a1}"
                return addr.a1

            cell.value = c.formula.render(ref)
            color = GREEN if cross else BLACK
        elif c.kind in ("input", "assumption"):
            cell.value = c.value
            color = BLUE
        else:
            cell.value = c.value
            color = BLACK
        cell.font = _font(color, bold=bold or c.bold)
        if c.kind != "text":
            cell.number_format = NUMBER_FORMATS.get(c.fmt, NUMBER_FORMATS["num"])
            cell.alignment = Alignment(horizontal="right")
        elif row.style == "colhead" and j >= 0:
            cell.alignment = Alignment(horizontal="right" if sheet not in ("Inputs", "Assumptions") else "left")
    _row_decor(ws, r, row, last)


def _row_decor(ws: Worksheet, r: int, row: Row, last: int) -> None:
    if row.style == "section":
        fill = PatternFill("solid", fgColor=BAR)
        for col in range(1, last + 1):
            c = ws.cell(row=r, column=col)
            c.fill = fill
            c.font = _font(WHITE, bold=True)
        return
    start = FIRST_DATA_COL if row.style in ("subtotal", "total") else 1
    for col in range(start, last + 1):
        c = ws.cell(row=r, column=col)
        if row.style in ("subtotal", "total"):
            if c.value is not None:
                c.border = Border(top=_THIN)
        elif row.style == "grand":
            c.fill = PatternFill("solid", fgColor=TOTAL_FILL)
            c.border = Border(top=_THIN, bottom=_DOUBLE) if col >= FIRST_DATA_COL and c.value is not None else Border()
        elif row.style == "colhead" and c.value is not None:
            c.border = Border(bottom=_THIN)
