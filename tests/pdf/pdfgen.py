"""Tiny PDF writer for test fixtures: places Helvetica text at exact positions.

No dependency, deterministic bytes. Real supplementals are laid out the same way at
the level the parser sees (positioned words), so fixtures built here exercise the
same code paths: column alignment, multi-line headers, scale notes, totals.

    doc = PdfDoc()
    p = doc.page()                       # US Letter landscape by default
    p.text(36, 40, "Debt Maturities", size=14, bold=True)
    p.row(36, 90, ["2027", "1,250", "4.1%"], xs=[36, 300, 400], align="right")
    data = doc.render()
"""

from __future__ import annotations

from dataclasses import dataclass, field


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


@dataclass
class Page:
    width: float = 792.0
    height: float = 612.0
    ops: list[str] = field(default_factory=list)

    def text(self, x: float, top: float, s: str, size: float = 8.0, bold: bool = False, right: bool = False) -> None:
        """Write s with its left edge (or right edge when right=True) at x, baseline at `top` from the page top."""
        if right:
            x -= _width(s, size, bold)
        font = "F2" if bold else "F1"
        y = self.height - top
        self.ops.append(f"BT /{font} {size:.2f} Tf {x:.2f} {y:.2f} Td ({_esc(s)}) Tj ET")

    def row(self, top: float, cells: list[str], xs: list[float], size: float = 8.0, bold: bool = False) -> None:
        """First cell left-aligned at xs[0]; the rest right-aligned on their x (numbers line up right)."""
        for i, (c, x) in enumerate(zip(cells, xs)):
            if c:
                self.text(x, top, c, size=size, bold=bold, right=i > 0)

    def cells(self, top: float, cells: list[str], xs: list[float], right_from: int = 1, size: float = 8.0) -> None:
        """Cells before index right_from are left-aligned on their x (text columns); the rest right-aligned."""
        for i, (c, x) in enumerate(zip(cells, xs)):
            if c:
                self.text(x, top, c, size=size, right=i >= right_from)


@dataclass
class PdfDoc:
    pages: list[Page] = field(default_factory=list)

    def page(self, landscape: bool = True) -> Page:
        p = Page() if landscape else Page(width=612.0, height=792.0)
        self.pages.append(p)
        return p

    def render(self) -> bytes:
        objs: list[bytes] = []
        n = len(self.pages)
        # 1 catalog, 2 pages, 3 F1, 4 F2, then (page, content) pairs
        kids = " ".join(f"{5 + 2 * i} 0 R" for i in range(n))
        objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
        objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
        objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        for i, p in enumerate(self.pages):
            content = "\n".join(p.ops).encode("cp1252")
            objs.append(
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {p.width:.0f} {p.height:.0f}] "
                f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {6 + 2 * i} 0 R >>".encode()
            )
            objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, o in enumerate(objs, start=1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
        for off in offsets:
            out += f"{off:010d} 00000 n \n".encode()
        out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        return bytes(out)


# Helvetica advance widths (1/1000 em) for the characters fixtures use; others ~556.
_W = {
    " ": 278,
    ",": 278,
    ".": 278,
    "-": 333,
    "(": 333,
    ")": 333,
    "%": 889,
    "$": 556,
    "/": 278,
    ":": 278,
    "I": 278,
    "i": 222,
    "l": 222,
    "j": 222,
    "t": 278,
    "f": 278,
    "r": 333,
    "m": 833,
    "w": 722,
    "W": 944,
    "M": 833,
    "&": 667,
}
_WB = {
    " ": 278,
    ",": 278,
    ".": 278,
    "-": 333,
    "(": 333,
    ")": 333,
    "%": 889,
    "I": 278,
    "i": 278,
    "l": 278,
    "t": 333,
    "f": 333,
    "r": 389,
    "m": 889,
    "M": 833,
    "W": 944,
    "w": 778,
    "&": 722,
}


def _width(s: str, size: float, bold: bool) -> float:
    table = _WB if bold else _W
    return sum(table.get(ch, 556) for ch in s) * size / 1000.0
