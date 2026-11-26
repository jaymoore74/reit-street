"""Offline copy of the SEC endpoints the pipeline calls, in SEC's exact JSON and HTML
formats, for 12 REITs, plus a universe CSV and an IR website.

VALUES ARE SYNTHETIC. They exist to test parsing, versioning, Q4 derivation and
idempotency, never to be read as real financials.

Layout mirrors URLs: <root>/<host>/<path>, e.g. root/data.sec.gov/submissions/CIK0001045609.json

Built in scenarios the tests rely on:
  PLD  restates Q1 2024 revenue (+1%) in its Q1 2025 10-Q, and has a paged submissions file
  EQR  reports revenue under a lower-priority tag only
  O    reports both the primary and an alternate revenue tag (primary must win)
  all  balance sheets satisfy assets = liabilities + equity
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

REITS = {
    "PLD": (1045609, "Prologis, Inc."),
    "O": (726728, "Realty Income Corp"),
    "SPG": (1063761, "Simon Property Group Inc"),
    "EQR": (906107, "Equity Residential"),
    "WELL": (766704, "Welltower Inc."),
    "DLR": (1297996, "Digital Realty Trust, Inc."),
    "PSA": (1393311, "Public Storage"),
    "BXP": (1037540, "BXP, Inc."),
    "VTR": (740260, "Ventas, Inc."),
    "AMT": (1053507, "American Tower Corp"),
    "HST": (1070750, "Host Hotels & Resorts, Inc."),
    "ARE": (1035443, "Alexandria Real Estate Equities, Inc."),
}
IR_HOST = "ir.reit-fixture.test"
Q_ENDS_2024 = [date(2024, 3, 31), date(2024, 6, 30), date(2024, 9, 30)]
UNIVERSE_HEADER = (
    "ticker,company_name,cik,cik_source,exchange,sector,reit_type,listing_note,ir_home_url,results_page_url,"
    "latest_supplemental_pdf_url,latest_supplemental_quarter,email_alerts_url,ir_platform,verified,scrape_notes,"
    "corporate_events,notes"
)


def base_revenue(ticker: str) -> int:
    return 1_000_000_000 + list(REITS).index(ticker) * 150_000_000


def quarters_2024(ticker: str) -> list[int]:
    b = base_revenue(ticker)
    return [b, int(b * 1.02), int(b * 1.04), int(b * 1.06)]


def acc(cik: int, yy: int, seq: int) -> str:
    return f"{cik:010d}-{yy:02d}-{seq:06d}"


def write(root: Path, host: str, path: str, data: Any) -> None:
    f = root / host / path.lstrip("/")
    f.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, dict | list):
        f.write_text(json.dumps(data))
    elif isinstance(data, bytes):
        f.write_bytes(data)
    else:
        f.write_text(data)


def index_html(cik: int, accession: str, docs: list[tuple[str, str, str, int]]) -> str:
    rows = "\n".join(
        f'<tr><td scope="row">{i + 1}</td><td scope="row">{desc}</td>'
        f'<td scope="row"><a href="/Archives/edgar/data/{cik}/{accession.replace("-", "")}/{name}">{name}</a>'
        f'{" &nbsp;&nbsp;iXBRL" if i == 0 else ""}</td><td scope="row">{dtype}</td><td scope="row">{size}</td></tr>'
        for i, (desc, name, dtype, size) in enumerate(docs)
    )
    return (
        '<html><body><div id=\'formDiv\'><table class="tableFile" summary="Document Format Files">'
        '<tr><th scope="col">Seq</th><th scope="col">Description</th><th scope="col">Document</th>'
        f'<th scope="col">Type</th><th scope="col">Size</th></tr>\n{rows}\n</table></div></body></html>'
    )


def _block(rows: list[tuple[Any, ...]]) -> dict[str, list[Any]]:
    keys = [
        "accessionNumber",
        "filingDate",
        "reportDate",
        "acceptanceDateTime",
        "act",
        "form",
        "fileNumber",
        "filmNumber",
        "items",
        "core_type",
        "size",
        "isXBRL",
        "isInlineXBRL",
        "primaryDocument",
        "primaryDocDescription",
    ]
    cols: dict[str, list[Any]] = {k: [] for k in keys}
    for a, form, filed, report, items, doc, desc in rows:
        xbrl = 1 if form in ("10-K", "10-Q", "8-K") else 0
        vals = [
            a,
            filed.isoformat(),
            report.isoformat() if report else "",
            f"{filed.isoformat()}T16:05:12.000Z",
            "34",
            form,
            "001-13545",
            "24000000",
            items,
            form,
            123456,
            xbrl,
            xbrl,
            doc,
            desc,
        ]
        for k, v in zip(keys, vals, strict=True):
            cols[k].append(v)
    return cols


def _fact(
    start: date | None,
    end: date,
    val: float,
    a: str,
    fy: int,
    fp: str,
    form: str,
    filed: date,
    frame: str | None = None,
) -> dict[str, Any]:
    f: dict[str, Any] = {
        "end": end.isoformat(),
        "val": val,
        "accn": a,
        "fy": fy,
        "fp": fp,
        "form": form,
        "filed": filed.isoformat(),
    }
    if start:
        f = {"start": start.isoformat(), **f}
    if frame:
        f["frame"] = frame
    return f


def build(root: Path) -> Path:
    root = Path(root)
    tick = {"fields": ["cik", "name", "ticker", "exchange"], "data": [[c, n, t, "NYSE"] for t, (c, n) in REITS.items()]}
    write(root, "www.sec.gov", "/files/company_tickers_exchange.json", tick)
    universe = [UNIVERSE_HEADER]
    d = date
    for n, (t, (cik, name)) in enumerate(REITS.items()):
        lower = t.lower()
        filings: list[tuple[Any, ...]] = []

        def add(
            a: str,
            form: str,
            filed: date,
            report: date | None,
            items: str = "",
            doc: str | None = None,
            desc: str = "",
            _l: str = lower,
            _f: list[tuple[Any, ...]] = filings,
        ) -> None:
            _f.append((a, form, filed, report, items, doc or f"{_l}-{report:%Y%m%d}.htm", desc))

        a10k23 = acc(cik, 24, 5)
        add(a10k23, "10-K", d(2024, 2, 15), d(2023, 12, 31), desc="10-K")
        a10q = []
        for i, qe in enumerate(Q_ENDS_2024):
            add(
                acc(cik, 24, 20 + i * 10),
                "8-K",
                d(qe.year, qe.month + 1, 15),
                qe,
                "2.02,9.01",
                f"{lower}-8k-{qe:%Y%m%d}.htm",
                "8-K",
            )
            aq = acc(cik, 24, 21 + i * 10)
            add(aq, "10-Q", d(qe.year, qe.month + 1, 30), qe, desc="10-Q")
            a10q.append(aq)
        a10k24 = acc(cik, 25, 5)
        add(a10k24, "10-K", d(2025, 2, 14), d(2024, 12, 31), desc="10-K")
        a10q25 = acc(cik, 25, 21)
        add(a10q25, "10-Q", d(2025, 4, 30), d(2025, 3, 31), desc="10-Q")
        add(acc(cik, 25, 30), "DEF 14A", d(2025, 3, 20), None, doc="def14a.htm", desc="DEF 14A")
        old = (
            [(acc(cik, 23, 5), "10-K", d(2023, 2, 14), d(2022, 12, 31), "", "pld-20221231.htm", "10-K")]
            if t == "PLD"
            else []
        )
        recent = sorted(filings, key=lambda r: r[2], reverse=True)
        page = f"CIK{cik:010d}-submissions-001.json"
        sub = {
            "cik": str(cik),
            "entityType": "operating",
            "sic": "6798",
            "sicDescription": "Real Estate Investment Trusts",
            "name": name,
            "tickers": [t],
            "exchanges": ["NYSE"],
            "fiscalYearEnd": "1231",
            "stateOfIncorporation": "MD",
            "formerNames": [
                {"name": name.upper() + " OLD", "from": "2001-01-01T00:00:00.000Z", "to": "2011-06-03T00:00:00.000Z"}
            ]
            if t == "PLD"
            else [],
            "filings": {
                "recent": _block(recent),
                "files": [{"name": page, "filingCount": len(old), "filingFrom": "2023-02-14", "filingTo": "2023-02-14"}]
                if old
                else [],
            },
        }
        write(root, "data.sec.gov", f"/submissions/CIK{cik:010d}.json", sub)
        if old:
            write(root, "data.sec.gov", f"/submissions/{page}", _block(old))

        for a, form, filed, report, _items, doc, _desc in filings:
            nd = a.replace("-", "")
            if form == "8-K":
                ex1, ex2 = f"{lower}-ex991_{report:%Y%m%d}.htm", f"{lower}-ex992_{report:%Y%m%d}.pdf"
                docs = [
                    ("8-K", doc, "8-K", 30000),
                    ("PRESS RELEASE DATED " + filed.isoformat(), ex1, "EX-99.1", 90000),
                    ("SUPPLEMENTAL INFORMATION PACKAGE", ex2, "EX-99.2", 900000),
                    ("GRAPHIC", "logo.jpg", "GRAPHIC", 5000),
                ]
                write(
                    root,
                    "www.sec.gov",
                    f"/Archives/edgar/data/{cik}/{nd}/{ex1}",
                    f"<html>{name} results for the period ended {report}</html>",
                )
                write(
                    root, "www.sec.gov", f"/Archives/edgar/data/{cik}/{nd}/{ex2}", b"%PDF-1.4 synthetic " + a.encode()
                )
            elif form in ("10-K", "10-Q"):
                docs = [
                    (form, doc, form, 2000000),
                    ("EXHIBIT 31.1", "ex311.htm", "EX-31.1", 9000),
                    ("XBRL INSTANCE", f"{lower}-{report:%Y%m%d}_htm.xml", "XML", 500000),
                ]
            else:
                continue
            write(root, "www.sec.gov", f"/Archives/edgar/data/{cik}/{nd}/{a}-index.htm", index_html(cik, a, docs))

        q = quarters_2024(t)
        b = base_revenue(t)
        fy23 = int(b * 3.9)
        rev = [
            _fact(d(2023, 1, 1), d(2023, 12, 31), fy23, a10k23, 2023, "FY", "10-K", d(2024, 2, 15), "CY2023"),
            _fact(d(2024, 1, 1), d(2024, 3, 31), q[0], a10q[0], 2024, "Q1", "10-Q", d(2024, 4, 30), "CY2024Q1"),
            _fact(d(2024, 4, 1), d(2024, 6, 30), q[1], a10q[1], 2024, "Q2", "10-Q", d(2024, 7, 30), "CY2024Q2"),
            _fact(d(2024, 1, 1), d(2024, 6, 30), q[0] + q[1], a10q[1], 2024, "Q2", "10-Q", d(2024, 7, 30)),
            _fact(d(2024, 7, 1), d(2024, 9, 30), q[2], a10q[2], 2024, "Q3", "10-Q", d(2024, 10, 30), "CY2024Q3"),
            _fact(d(2024, 1, 1), d(2024, 9, 30), q[0] + q[1] + q[2], a10q[2], 2024, "Q3", "10-Q", d(2024, 10, 30)),
            _fact(d(2023, 1, 1), d(2023, 12, 31), fy23, a10k24, 2024, "FY", "10-K", d(2025, 2, 14)),
            _fact(d(2024, 1, 1), d(2024, 12, 31), sum(q), a10k24, 2024, "FY", "10-K", d(2025, 2, 14), "CY2024"),
            _fact(d(2025, 1, 1), d(2025, 3, 31), int(b * 1.08), a10q25, 2025, "Q1", "10-Q", d(2025, 4, 30), "CY2025Q1"),
            _fact(
                d(2024, 1, 1),
                d(2024, 3, 31),
                int(q[0] * 1.01) if t == "PLD" else q[0],
                a10q25,
                2025,
                "Q1",
                "10-Q",
                d(2025, 4, 30),
            ),
        ]
        ni = [dict(x, val=int(x["val"] * 0.3)) for x in rev]
        assets, liabs, equity = [], [], []
        for a, form, filed, report, *_r in filings:
            if form in ("10-Q", "10-K"):
                v = int(b * 40 * (1 + (report.year - 2023) * 0.05 + report.month / 400))
                fp = "FY" if form == "10-K" else f"Q{(report.month - 1) // 3 + 1}"
                frame = f"CY{report.year}Q{(report.month - 1) // 3 + 1}I"
                assets.append(_fact(None, report, v, a, report.year, fp, form, filed, frame))
                liabs.append(_fact(None, report, int(v * 0.55), a, report.year, fp, form, filed, frame))
                equity.append(_fact(None, report, v - int(v * 0.55), a, report.year, fp, form, filed, frame))
        shares = [_fact(None, d(2025, 4, 25), 925_000_000 + n, a10q25, 2025, "Q1", "10-Q", d(2025, 4, 30))]
        eps = [_fact(d(2024, 1, 1), d(2024, 12, 31), 3.15 + n / 10, a10k24, 2024, "FY", "10-K", d(2025, 2, 14))]
        rev_tag = {"EQR": "RevenueFromContractWithCustomerExcludingAssessedTax"}.get(t, "Revenues")
        gaap: dict[str, Any] = {
            rev_tag: {"label": "Revenues", "description": "Revenue", "units": {"USD": rev}},
            "NetIncomeLoss": {"label": "Net Income (Loss)", "description": "", "units": {"USD": ni}},
            "Assets": {"label": "Assets", "description": "", "units": {"USD": assets}},
            "Liabilities": {"label": "Liabilities", "description": "", "units": {"USD": liabs}},
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": {
                "label": "Equity",
                "description": "",
                "units": {"USD": equity},
            },
            "EarningsPerShareDiluted": {"label": "EPS diluted", "description": "", "units": {"USD/shares": eps}},
        }
        if t == "O":
            gaap["RevenueFromContractWithCustomerExcludingAssessedTax"] = {
                "label": "Rev contract",
                "description": "",
                "units": {"USD": [dict(rev[7], val=123)]},
            }
        facts = {
            "cik": cik,
            "entityName": name,
            "facts": {
                "dei": {
                    "EntityCommonStockSharesOutstanding": {
                        "label": "Shares",
                        "description": "",
                        "units": {"shares": shares},
                    }
                },
                "us-gaap": gaap,
            },
        }
        write(root, "data.sec.gov", f"/api/xbrl/companyfacts/CIK{cik:010d}.json", facts)

        supp = f"https://{IR_HOST}/{lower}/latest-supplemental.pdf" if t in ("PLD", "O") else ""
        if supp:
            write(
                root,
                IR_HOST,
                f"/{lower}/latest-supplemental.pdf",
                b"%PDF-1.4 synthetic IR supplemental v1 " + t.encode(),
            )
        universe.append(
            f'{t},"{name}",{cik},fixture,NYSE,Industrial,Equity,,https://{IR_HOST}/{lower}/,'
            f"https://{IR_HOST}/{lower}/results/,{supp},Q3 2026,,fixture,Y,synthetic fixture row,,"
        )
    (root / "reits.csv").write_text("\n".join(universe) + "\n", encoding="utf-8")
    return root


if __name__ == "__main__":
    import sys

    print(build(Path(sys.argv[1] if len(sys.argv) > 1 else "fixtures")))
