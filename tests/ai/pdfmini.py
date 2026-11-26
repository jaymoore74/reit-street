"""A tiny PDF writer for tests: text lines at given positions, Helvetica, one or more pages."""

from __future__ import annotations


def make_pdf(pages: list[list[tuple[float, float, str]]]) -> bytes:
    objs: list[bytes] = []

    def add(body: str | bytes) -> int:
        objs.append(body.encode("latin-1") if isinstance(body, str) else body)
        return len(objs)

    font = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pages_id = len(objs) + 1 + 2 * len(pages)  # placeholder index computed after page objects
    kids = []
    for lines in pages:
        ops = ["BT", "/F1 9 Tf"]
        for x, y, text in lines:
            t = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"1 0 0 1 {x} {y} Tm ({t}) Tj")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page = add(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font} 0 R >> >> /Contents {content} 0 R >>"
        )
        kids.append(page)
    assert add(f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>") == pages_id
    catalog = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


SUPPLEMENTAL_PAGE = [
    (50, 740, "Acme Realty Trust"),
    (50, 725, "Same-Property Net Operating Income"),
    (50, 712, "(in thousands, except percentages)"),
    (300, 695, "Q3 2024"),
    (400, 695, "Q3 2023"),
    (50, 680, "Rental revenues"),
    (300, 680, "125,400"),
    (400, 680, "120,100"),
    (50, 665, "Property operating expenses"),
    (300, 665, "(41,200)"),
    (400, 665, "(40,050)"),
    (50, 650, "Same-property NOI"),
    (300, 650, "84,200"),
    (400, 650, "80,050"),
    (50, 635, "NOI growth"),
    (300, 635, "5.2%"),
    (50, 620, "Occupancy"),
    (300, 620, "96.1%"),
    (400, 620, "95.4%"),
]
