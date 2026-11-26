from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from reitstreet.core.errors import ParseError
from reitstreet.parsers import sec_companyfacts, sec_index, sec_submissions
from reitstreet.parsers.documents import DocMeta, ManualCsvParser, parse_number, pick
from reitstreet.reference.universe import parse_universe_csv
from tests.sec_fixtures import REITS

CIK = REITS["PLD"][0]


def test_submissions(sec_root: Path) -> None:
    sub = sec_submissions.parse_submissions((sec_root / f"data.sec.gov/submissions/CIK{CIK:010d}.json").read_bytes())
    assert sub.name == "Prologis, Inc." and sub.fiscal_year_end == "1231"
    assert len(sub.filings) == 10 and sub.page_files == [f"CIK{CIK:010d}-submissions-001.json"]
    k = next(f for f in sub.filings if f["form_type"] == "10-K")
    assert k["accession_no"].startswith(f"{CIK:010d}-")
    page = sec_submissions.parse_submissions_page(
        (sec_root / f"data.sec.gov/submissions/CIK{CIK:010d}-submissions-001.json").read_bytes()
    )
    assert [p["report_date"] for p in page] == [date(2022, 12, 31)]
    with pytest.raises(ParseError):
        sec_submissions.parse_submissions(b"<html>rate limited</html>")


def test_index_and_exhibit_classification(sec_root: Path) -> None:
    acc = f"{CIK:010d}-24-000020"
    html = (sec_root / f"www.sec.gov/Archives/edgar/data/{CIK}/{acc.replace('-', '')}/{acc}-index.htm").read_text()
    docs = sec_index.parse_index_html(html)
    assert [d["doc_type"] for d in docs] == ["8-K", "EX-99.1", "EX-99.2", "GRAPHIC"]
    assert docs[0]["document_name"].endswith(".htm")  # no &nbsp; iXBRL suffix
    assert [sec_index.classify_exhibit(d) for d in docs] == [None, "earnings_release", "supplemental", None]
    assert sec_index.wants_index("8-K", "2.02,9.01") and not sec_index.wants_index("8-K", "5.07")
    assert sec_index.wants_index("10-Q", "") and not sec_index.wants_index("DEF 14A", "")


def test_companyfacts(sec_root: Path) -> None:
    concepts, facts = sec_companyfacts.parse_companyfacts(
        (sec_root / f"data.sec.gov/api/xbrl/companyfacts/CIK{CIK:010d}.json").read_bytes()
    )
    fl = list(facts)
    assert {c.tag for c in concepts} >= {"Revenues", "Assets", "EntityCommonStockSharesOutstanding"}
    assert len(fl) == 40
    with pytest.raises(ParseError):
        sec_companyfacts.parse_companyfacts(b"[]")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,234", "1234"),
        ("(1,234.5)", "-1234.5"),
        ("$ 12", "12"),
        ("4.5%", "4.5"),
        ("-3", "-3"),
        ("—", None),
        ("", None),
        ("n/a", None),
        ("abc", None),
        (None, None),
    ],
)
def test_parse_number(text: str | None, expected: str | None) -> None:
    assert parse_number(text) == (Decimal(expected) if expected else None)


def _meta(name: str, mime: str = "text/csv") -> DocMeta:
    return DocMeta(
        doc_id=1,
        company_id=1,
        doc_kind="supplemental",
        source="manual_archive",
        mime_type=mime,
        original_filename=name,
        period_end_date=date(2024, 9, 30),
    )


def test_manual_csv_parser() -> None:
    assert isinstance(pick(_meta("x.csv")), ManualCsvParser)
    import reitstreet.parsers.run  # noqa: F401  (registers the PDF parser)
    from reitstreet.parsers.pdf.parser import SupplementalPdfParser

    assert isinstance(pick(_meta("x.pdf", "application/pdf")), SupplementalPdfParser)
    res = ManualCsvParser().parse(b'row_label,value,page_no\nFFO,"(1,000)",4\nNote,n/a,4\n', _meta("x.csv"))
    assert [(i.row_label, i.value_numeric, i.page_no) for i in res.items] == [
        ("FFO", Decimal("-1000"), 4),
        ("Note", None, 4),
    ]
    with pytest.raises(ParseError):
        ManualCsvParser().parse(b"a,b\n1,2\n", _meta("x.csv"))


def test_universe_csv(sec_root: Path) -> None:
    rows = parse_universe_csv((sec_root / "reits.csv").read_bytes())
    assert len(rows) == 12 and rows[0].ticker == "PLD" and rows[0].cik == CIK and rows[0].verified == 1
    assert rows[0].ir_urls["latest_supplemental_pdf_url"].endswith("latest-supplemental.pdf")
    assert parse_universe_csv(b"\xef\xbb\xbfTicker,CIK\nabc,\n")[0].cik is None  # BOM, case, missing CIK
