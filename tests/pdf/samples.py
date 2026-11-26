"""Synthetic supplemental pages, one per layout the parser must handle.

Each builder returns PDF bytes. Numbers are made up; the layouts follow what REIT
supplementals actually print (stacked period headers, scale notes, records tables with
Thereafter and Total rows, per-share lines, same-store change columns).
"""

from __future__ import annotations

from .pdfgen import PdfDoc


def ffo_and_debt(year: int = 2026, doc: PdfDoc | None = None) -> bytes:
    """Office/industrial style: an FFO reconciliation and a debt maturity ladder on one page."""
    d = doc or PdfDoc()
    p = d.page()
    p.text(36, 40, "Funds From Operations", size=14, bold=True)
    p.text(36, 56, "(dollars in thousands, except per share data)", size=8)
    xs = [36, 330, 410, 520, 600]
    p.text(290, 80, "Three Months Ended September 30,", size=8, bold=True)
    p.text(480, 80, "Nine Months Ended September 30,", size=8, bold=True)
    p.row(92, ["", str(year), str(year - 1), str(year), str(year - 1)], xs, bold=True)
    rows = [
        ["Net income attributable to common", "$ 114,482", "$ 98,100", "$ 330,111", "$ 301,002"],
        ["Depreciation and amortization", "120,300", "115,220", "355,800", "340,100"],
        ["Gain on sale of real estate", "(12,004)", "-", "(40,500)", "(3,200)"],
        ["FFO attributable to common", "$ 222,778", "$ 213,320", "$ 645,411", "$ 637,902"],
        ["FFO per diluted share", "$ 1.21", "$ 1.15", "$ 3.50", "$ 3.44"],
    ]
    for i, r in enumerate(rows):
        p.row(108 + 14 * i, r, xs)
    p.text(36, 220, "Debt Maturities", size=12, bold=True)
    xs2 = [36, 200, 290, 380, 460, 540]
    p.row(240, ["Year", "Secured", "Unsecured", "Total", "Wtd. Avg. Rate", "% of Total"], xs2, bold=True)
    ladder = [
        [str(year + 1), "131,449", "-", "131,449", "4.43%", "6%"],
        [str(year + 2), "12,000", "300,000", "312,000", "3.90%", "14%"],
        ["Thereafter", "-", "1,800,000", "1,800,000", "4.10%", "80%"],
        ["Total", "143,449", "2,100,000", "2,243,449", "4.08%", "100%"],
    ]
    for i, r in enumerate(ladder):
        p.row(256 + 14 * i, r, xs2)
    return d.render()


def net_lease_tenants(merged_headers: bool = False, year: int = 2026, doc: PdfDoc | None = None) -> bytes:
    """Net lease style: top tenants and a lease expiration schedule.

    merged_headers=True sets two header cells so close that they read as one, the way a
    tight layout sometimes extracts; those columns must not be mapped to fields.
    """
    d = doc or PdfDoc()
    p = d.page()
    p.text(36, 40, "Top 5 Tenants", size=14, bold=True)
    p.text(36, 56, f"As of September 30, {year} (dollars in thousands)", size=8)
    xs = [36, 260, 340, 430, 520] if merged_headers else [36, 250, 370, 450, 530]
    p.row(80, ["Tenant", "Properties", "Annualized Base Rent", "% of ABR", "Lease Term"], xs, bold=True)
    tenants = [
        ["7-Eleven", "148", "48,210", "5.6%", "9.1"],
        ["Mister Car Wash", "112", "33,004", "3.8%", "13.2"],
        ["LA Fitness", "31", "28,450", "3.3%", "7.4"],
        ["Dave & Buster's", "29", "25,900", "3.0%", "11.0"],
        ["BJ's Wholesale Club", "12", "22,100", "2.6%", "8.2"],
    ]
    for i, r in enumerate(tenants):
        p.row(96 + 14 * i, r, xs)
    p.text(36, 200, "Lease Expirations", size=12, bold=True)
    xs2 = [36, 200, 290, 380]
    p.row(220, ["Year", "Number of Leases", "Annualized Base Rent", "% of ABR"], xs2, bold=True)
    exp = [
        [str(year), "12", "4,100", "0.5%"],
        [str(year + 1), "88", "31,250", "3.6%"],
        [str(year + 2), "121", "45,980", "5.3%"],
        ["Thereafter", "3,005", "780,200", "90.6%"],
        ["Total", "3,226", "861,530", "100.0%"],
    ]
    for i, r in enumerate(exp):
        p.row(236 + 14 * i, r, xs2)
    return d.render()


def apartment_same_store(year: int = 2026, doc: PdfDoc | None = None) -> bytes:
    """Apartment style: same-store results with a % change column, and a community list."""
    d = doc or PdfDoc()
    p = d.page()
    p.text(36, 40, "Same-Store Results", size=14, bold=True)
    p.text(36, 56, "(dollars in thousands)", size=8)
    xs = [36, 330, 410, 490]
    p.text(300, 80, "Three Months Ended September 30,", size=8, bold=True)
    p.row(92, ["", str(year), str(year - 1), "% Change"], xs, bold=True)
    rows = [
        ["Same-store revenue", "412,000", "398,100", "3.5%"],
        ["Same-store expenses", "140,800", "135,900", "3.6%"],
        ["Same-store NOI", "271,200", "262,200", "3.4%"],
        ["Average physical occupancy", "95.8%", "95.6%", "0.2%"],
    ]
    for i, r in enumerate(rows):
        p.row(108 + 14 * i, r, xs)
    p.text(36, 200, "Property Listing", size=12, bold=True)
    xs2 = [36, 180, 260, 330, 410]
    p.row(220, ["Community", "City", "State", "Units", "Occupancy"], xs2, bold=True)
    props = [
        ["The Grove", "Austin", "TX", "412", "96.1%"],
        ["Harbor Point", "Tampa", "FL", "288", "94.8%"],
        ["Elm Street Lofts", "Denver", "CO", "350", "95.5%"],
    ]
    for i, r in enumerate(props):
        p.cells(236 + 14 * i, r, xs2, right_from=3)
    return d.render()


def unreadable_scale(year: int = 2026, doc: PdfDoc | None = None) -> bytes:
    """A series table with no scale note anywhere: values are kept but quarantined."""
    d = doc or PdfDoc()
    p = d.page()
    p.text(36, 40, "Net Operating Income", size=14, bold=True)
    xs = [36, 330, 410]
    p.text(300, 64, "Three Months Ended September 30,", size=8, bold=True)
    p.row(76, ["", str(year), str(year - 1)], xs, bold=True)
    for i, r in enumerate([["Rental revenue", "512,000", "498,000"], ["Net operating income", "350,100", "340,200"]]):
        p.row(92 + 14 * i, r, xs)
    return d.render()


def combined(year: int = 2026) -> bytes:
    """A four-page supplemental with every layout above, one per page."""
    d = PdfDoc()
    ffo_and_debt(year, d)
    net_lease_tenants(year=year, doc=d)
    apartment_same_store(year, d)
    unreadable_scale(year, d)
    return d.render()
