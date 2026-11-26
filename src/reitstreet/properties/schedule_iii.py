"""Load 10-K Schedule III rows into stg_property_record (source_kind sec_schedule_iii).

Uses what sec-sync already indexed: stg_sec_filing (10-K, 10-K/A) and the documents on
each filing's index page. Fetches the XBRL instance (EX-101.INS, or EDGAR's extracted
instance "*_htm.xml" for inline filings) and the label linkbase (EX-101.LAB) through the
SEC fetcher, so every byte lands in the raw store and replay works.

Each 10-K gives one snapshot: one record per Schedule III row (property rows and
aggregate rows, flagged) plus one '__total__' aggregate record carrying the reported totals
for the reconciliation check.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from ..core.db import Db
from ..core.errors import ParseError
from ..core.http import Fetcher
from ..core.jobs import dead_letter
from ..parsers.sec_schedule_iii import PARSER_VERSION, ScheduleIII, parse_schedule_iii
from .records import PropertyRecord, TypeClassifier, stage_records

log = logging.getLogger(__name__)
LOADER = "sec_schedule_iii"
TOTAL_REF = "__total__"


def pick_documents(docs: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(instance, label linkbase) among a filing's index-page documents."""
    inst = lab = None
    for d in docs:
        t = (d.get("doc_type") or "").upper()
        name = (d.get("document_name") or "").lower()
        desc = (d.get("description") or "").upper()
        if t == "EX-101.INS" or "EXTRACTED XBRL INSTANCE" in desc or (name.endswith("_htm.xml") and inst is None):
            if inst is None or t == "EX-101.INS" or "EXTRACTED" in desc:
                inst = d
        elif t == "EX-101.LAB" or name.endswith("_lab.xml"):
            lab = d
    return inst, lab


def to_records(
    sched: ScheduleIII,
    *,
    company_id: int,
    company_sector: str | None,
    accession_no: str,
    filing_date: date,
    source_url: str,
    source_sha256: str,
    classifier: TypeClassifier | None,
) -> list[PropertyRecord]:
    if sched.as_of is None:
        return []
    base: dict[str, Any] = {
        "company_id": company_id, "source_kind": "sec_schedule_iii", "as_of_date": sched.as_of,
        "known_from": filing_date, "loader": LOADER, "loader_version": PARSER_VERSION, "accession_no": accession_no,
        "source_url": source_url, "source_sha256": source_sha256,
    }  # fmt: skip
    out: list[PropertyRecord] = []
    for i, r in enumerate(sched.rows):
        ref = r.property_member or r.row_key
        type_code = classifier.classify(r.type_hint, r.name, company_sector) if classifier else None
        out.append(
            PropertyRecord(
                **base,
                source_row_index=i,
                source_property_ref=ref[:255],
                name=r.name,
                city=r.city,
                state=r.state,
                country=r.country,
                raw_property_type=r.type_hint,
                type_code=type_code,
                year_built=r.year_built,
                year_renovated=r.year_renovated,
                acquired_text=r.acquired_text,
                acquired_date=r.acquired_date,
                is_aggregate=r.is_aggregate,
                aggregate_count=r.aggregate_count,
                attributes={"label": r.label, "row_key": r.row_key[:255]},
                **_money(r.values),
            )
        )
    if sched.totals:
        out.append(
            PropertyRecord(
                **base,
                source_property_ref=TOTAL_REF,
                name="Schedule III total",
                is_aggregate=True,
                attributes={"kind": "total", "facts_used": sched.facts_used},
                **_money(sched.totals),
            )
        )
    return out


MONEY = (
    "encumbrances", "initial_cost_land", "initial_cost_bldg", "costs_capitalized",
    "gross_land", "gross_bldg", "gross_total", "accum_depreciation",
)  # fmt: skip


def _money(values: dict[str, Any]) -> dict[str, Any]:
    return {k: values.get(k) for k in MONEY}


def load_company(
    db: Db, fetcher: Fetcher, company: dict[str, Any], run_id: int | None, *, years: int = 1, force: bool = False
) -> dict[str, int]:
    """Load Schedule III from the company's latest `years` annual reports (10-K, else 10-K/A)."""
    res = {"filings": 0, "records": 0, "property_rows": 0, "no_instance": 0, "no_schedule": 0, "parse_errors": 0}
    filings = db.query(
        """SELECT filing_id, accession_no, form_type, filing_date, report_date FROM stg_sec_filing
           WHERE company_id = :c AND form_type IN ('10-K','10-K/A','10-KT') AND docs_indexed_at IS NOT NULL
           ORDER BY report_date DESC, filing_date DESC""",
        {"c": company["company_id"]},
    )
    seen_periods: set[Any] = set()
    picked = []
    for f in filings:
        if f["report_date"] in seen_periods:
            continue  # the original 10-K wins over a later amendment for the same year
        seen_periods.add(f["report_date"])
        picked.append(f)
        if len(picked) >= years:
            break
    classifier = TypeClassifier.from_db(db)
    for f in picked:
        if not force and db.scalar(
            """SELECT 1 FROM stg_property_record WHERE company_id = :c AND source_kind = 'sec_schedule_iii'
               AND accession_no = :a AND retired_at IS NULL LIMIT 1""",
            {"c": company["company_id"], "a": f["accession_no"]},
        ):
            continue
        docs = db.query(
            "SELECT document_name, doc_type, description, url FROM stg_sec_filing_document WHERE filing_id = :f",
            {"f": f["filing_id"]},
        )
        inst, lab = pick_documents(docs)
        res["filings"] += 1
        if inst is None:
            res["no_instance"] += 1
            continue
        ri = fetcher.get(inst["url"], allow_404=True)
        if ri is None:
            res["no_instance"] += 1
            continue
        rl = fetcher.get(lab["url"], allow_404=True) if lab else None
        try:
            sched = parse_schedule_iii(ri.body, rl.body if rl else None)
        except ParseError as e:
            db.rollback()
            res["parse_errors"] += 1
            dead_letter(
                db, stage="property.schedule_iii.parse", item_key=f"{company['ticker']}:{f['accession_no']}",
                exc=e, run_id=run_id, sha256=ri.sha256, company_id=company["company_id"],
            )  # fmt: skip
            db.commit()
            continue
        recs = to_records(
            sched,
            company_id=company["company_id"],
            company_sector=company.get("sector"),
            accession_no=f["accession_no"],
            filing_date=f["filing_date"],
            source_url=inst["url"],
            source_sha256=ri.sha256,
            classifier=classifier,
        )
        if not recs:
            res["no_schedule"] += 1
            continue
        stage_records(db, recs, run_id)
        db.commit()
        res["records"] += len(recs)
        res["property_rows"] += sum(1 for r in recs if not r.is_aggregate)
    return res
