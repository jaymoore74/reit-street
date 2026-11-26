"""Run document parsers and load their output into staging."""

from __future__ import annotations

import json
from typing import Any

from ..core.db import Db
from ..core.errors import ParseError
from ..core.rawstore import RawStore
from .documents import DocMeta, ParseResult, pick


def meta_of(doc: dict[str, Any]) -> DocMeta:
    return DocMeta(
        doc_id=doc["doc_id"],
        company_id=doc["company_id"],
        doc_kind=doc["doc_kind"],
        source=doc["source"],
        mime_type=doc["mime_type"],
        original_filename=doc["original_filename"],
        period_end_date=doc["period_end_date"],
    )


def load_parse_result(
    db: Db, doc: dict[str, Any], parser_name: str, parser_version: str, res: ParseResult, run_id: int | None
) -> int:
    parse_id = db.execute(
        """INSERT INTO stg_doc_parse (doc_id, sha256, parser_name, parser_version, run_id, status, n_tables, n_items,
             finished_at) VALUES (:d, :s, :pn, :pv, :r, 'succeeded', :nt, :ni, CURRENT_TIMESTAMP(6))""",
        {
            "d": doc["doc_id"],
            "s": doc["sha256"],
            "pn": parser_name,
            "pv": parser_version,
            "r": run_id,
            "nt": len(res.tables),
            "ni": len(res.items),
        },
    )
    table_ids: dict[tuple[int, int], int] = {}
    for t in res.tables:
        table_ids[(t.page_no, t.table_index)] = db.execute(
            """INSERT INTO stg_doc_table (parse_id, doc_id, page_no, table_index, title, section, bbox)
               VALUES (:p, :d, :pg, :ti, :t, :s, :b)""",
            {
                "p": parse_id,
                "d": doc["doc_id"],
                "pg": t.page_no,
                "ti": t.table_index,
                "t": (t.title or None),
                "s": t.section,
                "b": json.dumps(t.bbox) if t.bbox else None,
            },
        )
    db.executemany(
        """INSERT INTO stg_doc_line_item (parse_id, table_id, doc_id, page_no, row_index, col_index, row_label,
             col_label, raw_text, value_numeric, scale_hint, unit_hint, period_start, period_end, segment_label,
             confidence)
           VALUES (:p, :t, :d, :pg, :ri, :ci, :rl, :cl, :rt, :v, :sh, :uh, :ps, :pe, :seg, :conf)""",
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
                "rt": (i.raw_text or None) and i.raw_text[:255],
                "v": i.value_numeric,
                "sh": i.scale_hint,
                "uh": i.unit_hint,
                "ps": i.period_start,
                "pe": i.period_end,
                "seg": i.segment_label,
                "conf": i.confidence,
            }
            for i in res.items
        ),
    )
    status = "parsed" if any(i.value_numeric is not None for i in res.items) else "needs_review"
    db.execute(
        "UPDATE stg_document SET current_parse_id=:p, status=:s, status_note=:n WHERE doc_id=:d",
        {
            "p": parse_id,
            "s": status,
            "d": doc["doc_id"],
            "n": None if status == "parsed" else f"{parser_name} {parser_version} found no numeric values",
        },
    )
    return parse_id


def parse_document(db: Db, store: RawStore, doc: dict[str, Any], run_id: int | None) -> str:
    """Returns the new document status, or 'no_parser'. Raises ParseError for the dead-letter queue."""
    meta = meta_of(doc)
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
