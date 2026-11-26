"""Run document parsers and load their output into staging."""

from __future__ import annotations

import json
from typing import Any

from ..core.db import Db
from ..core.errors import ParseError
from ..core.rawstore import RawStore
from .documents import DocMeta, Override, ParseResult, pick, register
from .pdf.parser import SupplementalPdfParser, overrides_from_rows

register(SupplementalPdfParser())


def meta_of(doc: dict[str, Any], fye_month: int = 12, overrides: tuple[Override, ...] = ()) -> DocMeta:
    return DocMeta(
        doc_id=doc["doc_id"],
        company_id=doc["company_id"],
        doc_kind=doc["doc_kind"],
        source=doc["source"],
        mime_type=doc["mime_type"],
        original_filename=doc["original_filename"],
        period_end_date=doc["period_end_date"],
        fiscal_year_end_month=fye_month,
        overrides=overrides,
    )


def company_settings(db: Db, doc: dict[str, Any]) -> tuple[int, tuple[Override, ...]]:
    """Fiscal year-end month and active parse overrides for the document's company and period."""
    if not doc.get("company_id"):
        return 12, ()
    fye = db.scalar("SELECT fiscal_year_end FROM ref_company WHERE company_id=:c", {"c": doc["company_id"]})
    month = int(fye[:2]) if fye and str(fye)[:2].isdigit() and 1 <= int(fye[:2]) <= 12 else 12
    rows = db.query(
        """SELECT override_id, action, title_regex, page_from, page_to, params FROM ref_parse_override
           WHERE company_id=:c AND is_active=1
             AND (valid_from IS NULL OR :pe IS NULL OR :pe >= valid_from)
             AND (valid_to IS NULL OR :pe IS NULL OR :pe <= valid_to)
           ORDER BY override_id""",
        {"c": doc["company_id"], "pe": doc.get("period_end_date")},
    )
    return month, overrides_from_rows(rows)


def _json(v: Any) -> str | None:
    return json.dumps(v, separators=(",", ":")) if v else None


def load_parse_result(
    db: Db, doc: dict[str, Any], parser_name: str, parser_version: str, res: ParseResult, run_id: int | None
) -> int:
    n_quarantined = sum(1 for i in res.items if i.quarantined) + sum(1 for r in res.records if r.quarantined)
    parse_id = db.execute(
        """INSERT INTO stg_doc_parse (doc_id, sha256, parser_name, parser_version, config_hash, run_id, status,
             n_tables, n_items, n_records, n_quarantined, finished_at)
           VALUES (:d, :s, :pn, :pv, :ch, :r, 'succeeded', :nt, :ni, :nr, :nq, CURRENT_TIMESTAMP(6))""",
        {
            "d": doc["doc_id"],
            "s": doc["sha256"],
            "pn": parser_name,
            "pv": parser_version,
            "ch": res.config_hash,
            "r": run_id,
            "nt": len(res.tables),
            "ni": len(res.items),
            "nr": len(res.records),
            "nq": n_quarantined,
        },
    )
    table_ids: dict[tuple[int, int], int] = {}
    for t in res.tables:
        table_ids[(t.page_no, t.table_index)] = db.execute(
            """INSERT INTO stg_doc_table (parse_id, doc_id, page_no, table_index, title, section, table_kind,
                 orientation, kind_score, scale_text, scale_factor, scale_source, headers, n_rows, n_cols,
                 total_check, as_of_date, bbox)
               VALUES (:p, :d, :pg, :ti, :t, :s, :k, :o, :ks, :st, :sf, :ss, :h, :nr, :nc, :tc, :ao, :b)""",
            {
                "p": parse_id,
                "d": doc["doc_id"],
                "pg": t.page_no,
                "ti": t.table_index,
                "t": (t.title or None) and t.title[:512],
                "s": (t.section or None) and t.section[:255],
                "k": t.table_kind,
                "o": t.orientation,
                "ks": t.kind_score,
                "st": (t.scale_text or None) and t.scale_text[:128],
                "sf": t.scale_factor,
                "ss": t.scale_source,
                "h": _json(t.headers),
                "nr": t.n_rows,
                "nc": t.n_cols,
                "tc": t.total_check,
                "ao": t.as_of,
                "b": _json(t.bbox),
            },
        )
    db.executemany(
        """INSERT INTO stg_doc_line_item (parse_id, table_id, doc_id, page_no, row_index, col_index, row_label,
             col_label, metric_label, raw_text, value_numeric, scale_hint, unit_hint, period_start, period_end,
             period_type, segment_label, group_label, col_role, bbox, confidence, quarantined, quarantine_reason)
           VALUES (:p, :t, :d, :pg, :ri, :ci, :rl, :cl, :ml, :rt, :v, :sh, :uh, :ps, :pe, :pt, :seg, :grp, :role,
             :bb, :conf, :q, :qr)""",
        (
            {
                "p": parse_id,
                "t": table_ids.get((i.page_no, i.table_index)) if i.table_index else None,
                "d": doc["doc_id"],
                "pg": i.page_no,
                "ri": i.row_index,
                "ci": i.col_index,
                "rl": i.row_label[:512],
                "cl": (i.col_label or None) and i.col_label[:255],
                "ml": (i.metric_label or None) and i.metric_label[:512],
                "rt": (i.raw_text or None) and i.raw_text[:255],
                "v": i.value_numeric,
                "sh": i.scale_hint,
                "uh": i.unit_hint,
                "ps": i.period_start,
                "pe": i.period_end,
                "pt": i.period_type,
                "seg": (i.segment_label or None) and i.segment_label[:255],
                "grp": (i.group_label or None) and i.group_label[:255],
                "role": i.col_role,
                "bb": _json(i.bbox),
                "conf": i.confidence,
                "q": int(i.quarantined),
                "qr": (i.quarantine_reason or None) and i.quarantine_reason[:255],
            }
            for i in res.items
        ),
    )
    db.executemany(
        """INSERT INTO stg_doc_record (parse_id, table_id, doc_id, page_no, row_index, record_kind, label, fields,
             as_of_date, bbox, confidence, quarantined, quarantine_reason)
           VALUES (:p, :t, :d, :pg, :ri, :k, :l, :f, :ao, :bb, :conf, :q, :qr)""",
        (
            {
                "p": parse_id,
                "t": table_ids.get((r.page_no, r.table_index)),
                "d": doc["doc_id"],
                "pg": r.page_no,
                "ri": r.row_index,
                "k": r.record_kind,
                "l": r.label[:512],
                "f": json.dumps(r.fields, separators=(",", ":"), sort_keys=True),
                "ao": r.as_of,
                "bb": _json(r.bbox),
                "conf": r.confidence,
                "q": int(r.quarantined),
                "qr": (r.quarantine_reason or None) and r.quarantine_reason[:255],
            }
            for r in res.records
        ),
    )
    usable = any(i.value_numeric is not None and not i.quarantined for i in res.items) or any(
        not r.quarantined for r in res.records
    )
    status = "parsed" if usable else "needs_review"
    db.execute(
        "UPDATE stg_document SET current_parse_id=:p, status=:s, status_note=:n WHERE doc_id=:d",
        {
            "p": parse_id,
            "s": status,
            "d": doc["doc_id"],
            "n": None if status == "parsed" else f"{parser_name} {parser_version} found no usable values",
        },
    )
    return parse_id


def parse_document(db: Db, store: RawStore, doc: dict[str, Any], run_id: int | None) -> str:
    """Returns the new document status, or 'no_parser'. Raises ParseError for the dead-letter queue."""
    month, overrides = company_settings(db, doc)
    meta = meta_of(doc, month, overrides)
    parser = pick(meta)
    if parser is None:
        return "no_parser"
    data = store.get(doc["sha256"])
    try:
        res = parser.parse(data, meta)
    except ParseError:
        db.execute(
            """INSERT INTO stg_doc_parse (doc_id, sha256, parser_name, parser_version, run_id, status, finished_at,
                 error_message) VALUES (:d, :s, :pn, :pv, :r, 'failed', CURRENT_TIMESTAMP(6), 'see dead letter')""",
            {"d": doc["doc_id"], "s": doc["sha256"], "pn": parser.name, "pv": parser.version, "r": run_id},
        )
        db.execute(
            "UPDATE stg_document SET status='failed', status_note=:n WHERE doc_id=:d",
            {"d": doc["doc_id"], "n": f"{parser.name} {parser.version} failed"},
        )
        db.commit()
        raise
    load_parse_result(db, doc, parser.name, parser.version, res, run_id)
    return str(db.scalar("SELECT status FROM stg_document WHERE doc_id=:d", {"d": doc["doc_id"]}))


DOC_COLS = "doc_id, company_id, doc_kind, source, mime_type, original_filename, period_end_date, sha256, status"
