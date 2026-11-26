"""Workbook specification: sheets, rows and cells, with evaluation and validation.

A model is built as a ``Book`` of ``Sheet`` objects. Each row has a label in column A and cells in columns B
onward. A cell is one of:

* ``input``      a sourced hard number (blue). Lives on the Inputs sheet only.
* ``assumption`` an analyst estimate with a rationale (blue). Lives on the Assumptions sheet only.
* ``calc``       a ``Formula`` over other cell keys (black, or green when it reads another sheet).
* ``text``       a label or note.

``Book.evaluate`` computes every calc in Python; ``Book.validate`` enforces the house rules (every input cited,
every assumption explained, no hard-coded numbers in calc cells, no dangling or circular references).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Literal

from .formula import ALLOWED_CONSTANTS, Formula, FormulaError

CellKind = Literal["input", "assumption", "calc", "text"]
RowStyle = Literal["normal", "section", "subtotal", "total", "grand", "bold", "note", "colhead"]

HEADER_ROWS = 3  # ticker, sheet title, header bar; data starts on Excel row 4
FIRST_DATA_COL = 2  # column A holds labels


@dataclass
class Cell:
    kind: CellKind
    key: str | None = None
    value: float | str | None = None
    formula: Formula | None = None
    fmt: str = "num"
    source: str = ""
    page: str = ""
    note: str = ""
    bold: bool = False


def calc(key: str, expr: str, fmt: str = "num") -> Cell:
    return Cell("calc", key=key, formula=Formula.parse(expr), fmt=fmt)


def text(value: str, bold: bool = False) -> Cell:
    return Cell("text", value=value, bold=bold)


@dataclass
class Row:
    label: str
    cells: list[Cell | None] = field(default_factory=list)
    style: RowStyle = "normal"
    indent: int = 0


@dataclass
class Sheet:
    name: str
    title: str
    headers: list[str] = field(default_factory=list)
    unit_note: str = "(in $ thousands, except per share data)"
    label_width: float = 41.0
    col_widths: list[float] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)

    def section(self, label: str) -> None:
        self.rows.append(Row(label, style="section"))

    def colhead(self, label: str, heads: list[str]) -> None:
        self.rows.append(Row(label, [text(h, bold=True) for h in heads], style="colhead"))

    def line(self, label: str, *cells: Cell | None, style: RowStyle = "normal", indent: int = 0) -> None:
        self.rows.append(Row(label, list(cells), style=style, indent=indent))

    def blank(self) -> None:
        self.rows.append(Row(""))

    def note(self, label: str) -> None:
        self.rows.append(Row(label, style="note"))


@dataclass(frozen=True)
class Address:
    sheet: str
    row: int  # Excel row, 1-based
    col: int  # Excel column, 1-based

    @property
    def a1(self) -> str:
        return f"{col_letter(self.col)}{self.row}"


def col_letter(col: int) -> str:
    s = ""
    while col:
        col, r = divmod(col - 1, 26)
        s = chr(65 + r) + s
    return s


def sheet_ref(sheet: str) -> str:
    return "'" + sheet.replace("'", "''") + "'"


@dataclass
class Book:
    ticker: str
    company: str
    sheets: list[Sheet] = field(default_factory=list)
    groups: dict[str, list[str]] = field(default_factory=dict)
    meta: dict[str, str] = field(default_factory=dict)

    def sheet(self, name: str, title: str, **kw: object) -> Sheet:
        s = Sheet(name, title, **kw)  # type: ignore[arg-type]
        self.sheets.append(s)
        return s

    # ------------------------------------------------------------------ layout
    def cells(self) -> Iterator[tuple[Address, Cell]]:
        for sh in self.sheets:
            for i, row in enumerate(sh.rows):
                for j, c in enumerate(row.cells):
                    if c is not None:
                        yield Address(sh.name, HEADER_ROWS + 1 + i, FIRST_DATA_COL + j), c

    def index(self) -> dict[str, tuple[Address, Cell]]:
        out: dict[str, tuple[Address, Cell]] = {}
        for addr, c in self.cells():
            if c.key is None:
                continue
            if c.key in out:
                raise FormulaError(f"duplicate cell key {c.key!r}")
            if c.key in self.groups:
                raise FormulaError(f"cell key {c.key!r} clashes with a group name")
            out[c.key] = (addr, c)
        return out

    def group_range(self, name: str, idx: dict[str, tuple[Address, Cell]]) -> tuple[str, int, int, int]:
        """(sheet, col, first_row, last_row) for a group, which must be one contiguous column."""
        members = self.groups[name]
        addrs = [idx[k][0] for k in members]
        sheets = {a.sheet for a in addrs}
        cols = {a.col for a in addrs}
        rows = [a.row for a in addrs]
        if len(sheets) != 1 or len(cols) != 1 or rows != list(range(rows[0], rows[0] + len(rows))):
            raise FormulaError(f"group {name!r} is not one contiguous column")
        return addrs[0].sheet, addrs[0].col, rows[0], rows[-1]

    # ------------------------------------------------------------------ evaluation
    def evaluate(self) -> dict[str, float]:
        idx = self.index()
        values: dict[str, float] = {}
        active: set[str] = set()

        def scalar(key: str) -> float:
            if key in values:
                return values[key]
            if key not in idx:
                raise FormulaError(f"unknown cell key {key!r}")
            c = idx[key][1]
            if c.kind in ("input", "assumption"):
                assert isinstance(c.value, (int, float))
                values[key] = float(c.value)
                return values[key]
            if c.formula is None:
                raise FormulaError(f"cell {key!r} has no number")
            if key in active:
                raise FormulaError(f"circular reference through {key!r}")
            active.add(key)
            try:
                values[key] = c.formula.evaluate(scalar, group)
            except FormulaError as e:
                raise FormulaError(f"{key}: {e}") from None
            active.discard(key)
            return values[key]

        def group(name: str) -> list[float] | None:
            if name not in self.groups:
                return None
            return [scalar(k) for k in self.groups[name]]

        for key in idx:
            scalar(key)
        return values

    # ------------------------------------------------------------------ validation
    def validate(self, source_ids: set[str] | None = None) -> list[str]:
        problems: list[str] = []
        try:
            idx = self.index()
        except FormulaError as e:
            return [str(e)]
        for name in self.groups:
            try:
                self.group_range(name, idx)
            except (FormulaError, KeyError) as e:
                problems.append(f"group {name}: {e}")
        for addr, c in self.cells():
            where = f"{addr.sheet}!{addr.a1}"
            if c.kind == "input":
                if addr.sheet != "Inputs":
                    problems.append(f"{where}: input {c.key} must live on the Inputs sheet")
                if not c.source or not c.page:
                    problems.append(f"{where}: input {c.key} has no source and page")
                elif source_ids is not None and c.source not in source_ids:
                    problems.append(f"{where}: input {c.key} cites unknown source {c.source!r}")
            elif c.kind == "assumption":
                if addr.sheet != "Assumptions":
                    problems.append(f"{where}: assumption {c.key} must live on the Assumptions sheet")
                if not c.note:
                    problems.append(f"{where}: assumption {c.key} has no rationale")
            elif c.kind == "calc":
                assert c.formula is not None
                bad = [v for v in c.formula.constants() if v not in ALLOWED_CONSTANTS]
                if bad:
                    problems.append(f"{where}: {c.key} hard-codes {bad} (move it to Inputs or Assumptions)")
                for n in c.formula.names():
                    if n not in idx and n not in self.groups:
                        problems.append(f"{where}: {c.key} refers to unknown key {n!r}")
            elif c.kind == "text" and isinstance(c.value, (int, float)):
                problems.append(f"{where}: number stored as text cell")
        if not problems:
            try:
                self.evaluate()
            except FormulaError as e:
                problems.append(str(e))
        return problems
