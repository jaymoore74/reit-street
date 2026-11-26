"""Load parsed SEC records into staging. Every function is idempotent: loading the
same raw object twice changes nothing, so sync and replay share these loaders."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

from ...core.db import Db
from ...parsers import sec_companyfacts, sec_index, sec_submissions
from ...parsers.sec_companyfacts import Concept, Fact
from ...parsers.sec_submissions import Submissions
from . import urls


def load_submissions(db: Db, company: dict[str, Any], sha: str, sub: Submissions, run_id: int | None) -> int:
    cid, cik = company["company_id"], int(company["cik"])
    db.execute(
        """UPDATE ref_company SET name=COALESCE(:n, name), sic=:sic, sic_description=:sd, entity_type=:et,
             fiscal_year_end=:fye, state_of_incorporation=:soi, submissions_sha256=:sha,
             submissions_synced_at=CURRENT_TIMESTAMP(6) WHERE company_id=:c""",
        {
            "n": sub.name,
            "sic": sub.sic,
            "sd": sub.sic_description,
            "et": sub.entity_type,
            "fye": sub.fiscal_year_end,
            "soi": sub.state_of_incorporation,
            "sha": sha,
            "c": cid,
        },
    )
    today = date.today()
    for t, ex in sub.tickers:
        db.execute(
            """INSERT INTO ref_company_ticker_history (company_id, ticker, exchange, first_seen, last_seen)
               VALUES (:c, :t, :ex, :d, :d) AS new ON DUPLICATE KEY UPDATE last_seen=new.last_seen,
               exchange=COALESCE(new.exchange, ref_company_ticker_history.exchange)""",
            {"c": cid, "t": t[:16], "ex": ex, "d": today},
        )
    for name, vf, vt in sub.former_names:
        db.execute(
            """INSERT IGNORE INTO ref_company_name_history (company_id, name, valid_from, valid_to)
               VALUES (:c, :n, :f, :t)""",
            {"c": cid, "n": name[:255], "f": vf, "t": vt},
        )
    return load_filings(db, cid, cik, sha, sub.filings, run_id)


def load_filings(
    db: Db, company_id: int, cik: int, sha: str, filings: Iterable[dict[str, Any]], run_id: int | None
) -> int:
    rows = []
    for f in filings:
        acc = f["accession_no"]
        rows.append(
            {
                **f,
                "c": company_id,
                "index_url": urls.filing_index_url(cik, acc),
                "primary_doc_url": urls.filing_doc_url(cik, acc, f["primary_document"])
                if f["primary_document"]
                else None,
                "sha": sha,
                "pv": sec_submissions.PARSER_VERSION,
                "run": run_id,
                "items": (f["items"] or None) and str(f["items"])[:255],
                "primary_doc_description": (f["primary_doc_description"] or None)
                and str(f["primary_doc_description"])[:255],
            }
        )
    return db.executemany(
        """INSERT IGNORE INTO stg_sec_filing (company_id, accession_no, form_type, filing_date, report_date,
             acceptance_datetime, act, file_number, film_number, items, size_bytes, is_xbrl, is_inline_xbrl,
             primary_document, primary_doc_description, index_url, primary_doc_url, src_sha256, parser_version,
             first_seen_run_id)
           VALUES (:c, :accession_no, :form_type, :filing_date, :report_date, :acceptance_datetime, :act,
             :file_number, :film_number, :items, :size_bytes, :is_xbrl, :is_inline_xbrl, :primary_document,
             :primary_doc_description, :index_url, :primary_doc_url, :sha, :pv, :run)""",
        rows,
    )


def load_index(
    db: Db, company: dict[str, Any], filing: dict[str, Any], sha: str, docs: list[dict[str, Any]], run_id: int | None
) -> list[int]:
    """Store the filing's document list and register 8-K EX-99 exhibits. Returns new doc_ids."""
    cik = int(company["cik"])
    acc = filing["accession_no"]
    db.executemany(
        """INSERT IGNORE INTO stg_sec_filing_document (filing_id, document_name, seq, doc_type, description, url,
             size_bytes, src_sha256)
           VALUES (:fid, :document_name, :seq, :doc_type, :description, :url, :size_bytes, :sha)""",
        [
            {
                **d,
                "fid": filing["filing_id"],
                "url": urls.filing_doc_url(cik, acc, d["document_name"]),
                "sha": sha,
                "description": (d["description"] or None) and d["description"][:512],
            }
            for d in docs
        ],
    )
    new_docs: list[int] = []
    if (filing["form_type"] or "").upper().startswith("8-K"):
        for d in docs:
            kind = sec_index.classify_exhibit(d)
            if not kind:
                continue
            doc_id = db.execute(
                """INSERT IGNORE INTO stg_document (company_id, doc_kind, source, source_url, accession_no, filing_id,
                     original_filename, mime_type, period_end_date, published_date, registered_run_id)
                   VALUES (:c, :k, 'sec_8k_exhibit', :u, :acc, :fid, :fn, :mt, :pe, :pd, :run)""",
                {
                    "c": company["company_id"],
                    "k": kind,
                    "u": urls.filing_doc_url(cik, acc, d["document_name"]),
                    "acc": acc,
                    "fid": filing["filing_id"],
                    "fn": d["document_name"],
                    "mt": sec_index.mime_for(d["document_name"]),
                    "pe": filing["report_date"],
                    "pd": filing["filing_date"],
                    "run": run_id,
                },
            )
            if doc_id:
                new_docs.append(doc_id)
    db.execute(
        "UPDATE stg_sec_filing SET docs_indexed_at=CURRENT_TIMESTAMP(6), docs_index_sha256=:s WHERE filing_id=:f",
        {"s": sha, "f": filing["filing_id"]},
    )
    return new_docs


class ConceptCache:
    def __init__(self, db: Db):
        self.db = db
        self.ids = {
            (r["taxonomy"], r["tag"]): r["concept_id"]
            for r in db.query("SELECT concept_id, taxonomy, tag FROM stg_xbrl_concept")
        }

    def ensure(self, concepts: Iterable[Concept]) -> None:
        new = [c for c in concepts if (c.taxonomy, c.tag) not in self.ids]
        if not new:
            return
        self.db.executemany(
            """INSERT INTO stg_xbrl_concept (taxonomy, tag, label, description) VALUES (:tax, :tag, :l, :d) AS new
               ON DUPLICATE KEY UPDATE label=COALESCE(new.label, stg_xbrl_concept.label)""",
            [{"tax": c.taxonomy, "tag": c.tag, "l": c.label, "d": c.description} for c in new],
        )
        self.ids.update(
            {
                (r["taxonomy"], r["tag"]): r["concept_id"]
                for r in self.db.query("SELECT concept_id, taxonomy, tag FROM stg_xbrl_concept")
            }
        )


def load_companyfacts(
    db: Db,
    company: dict[str, Any],
    sha: str,
    concepts: list[Concept],
    facts: Iterable[Fact],
    run_id: int | None,
    cache: ConceptCache | None = None,
) -> int:
    cache = cache or ConceptCache(db)
    cache.ensure(concepts)
    cid = company["company_id"]
    rows = (
        {
            "c": cid,
            "k": cache.ids[(f.taxonomy, f.tag)],
            "u": f.unit,
            "ps": f.period_start,
            "pe": f.period_end,
            "v": f.value,
            "acc": f.accession_no,
            "fy": f.fy,
            "fp": f.fp,
            "form": f.form,
            "filed": f.filed,
            "frame": f.frame,
            "sha": sha,
            "run": run_id,
        }
        for f in facts
    )
    n = db.executemany(
        """INSERT IGNORE INTO stg_xbrl_fact (company_id, concept_id, unit, period_start, period_end, value,
             accession_no, fy, fp, form, filed, frame, src_sha256, first_seen_run_id)
           VALUES (:c, :k, :u, :ps, :pe, :v, :acc, :fy, :fp, :form, :filed, :frame, :sha, :run)""",
        rows,
        chunk=5000,
    )
    db.execute(
        "UPDATE ref_company SET facts_sha256=:s, facts_synced_at=CURRENT_TIMESTAMP(6) WHERE company_id=:c",
        {"s": sha, "c": cid},
    )
    return n


PARSER_VERSIONS = {
    "submissions": sec_submissions.PARSER_VERSION,
    "index": sec_index.PARSER_VERSION,
    "companyfacts": sec_companyfacts.PARSER_VERSION,
}
