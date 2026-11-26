"""SEC EDGAR endpoints. Fair-access policy: <= 10 requests/second and a User-Agent
with a real name and email on every request (https://www.sec.gov/os/accessing-edgar-data)."""

from __future__ import annotations

TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/"

FATAL_HINT = (
    "SEC refused the request. Check [sec] user_agent in config.ini (real name and email). If it is "
    "set, SEC may be throttling this IP: wait 10 minutes and lower requests_per_second."
)


def submissions_url(cik: int) -> str:
    return SUBMISSIONS_URL.format(cik=int(cik))


def submissions_page_url(name: str) -> str:
    return SUBMISSIONS_PAGE_URL.format(name=name)


def companyfacts_url(cik: int) -> str:
    return COMPANYFACTS_URL.format(cik=int(cik))


def filing_base(cik: int, accession: str) -> str:
    return ARCHIVE_BASE.format(cik=int(cik), acc_nodash=accession.replace("-", ""))


def filing_index_url(cik: int, accession: str) -> str:
    return filing_base(cik, accession) + f"{accession}-index.htm"


def filing_doc_url(cik: int, accession: str, document: str) -> str:
    return filing_base(cik, accession) + document


def edgar_company_url(cik: int) -> str:
    return f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={int(cik):010d}"
