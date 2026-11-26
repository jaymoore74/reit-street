"""Build wh_metric_value from staging. Bitemporal and idempotent.

Three time axes on every row:
  valid time      period_start / period_end: the business period the number describes
  knowledge time  known_from / known_to: when the market could know this value; a
                  restatement closes the old version (known_to) and opens a new one
  system time     recorded_at / retired_at: when this warehouse held the row; a rebuild
                  after a mapping or parser change retires rows instead of deleting them

The build computes the full desired set of rows for a company from staging (a pure
function of staging + reference data) and diffs it against the live rows by row_hash:
unchanged rows are untouched, vanished rows are retired, new rows are inserted. A
rebuild with no upstream change writes nothing.

XBRL path:     stg_xbrl_fact -> ref_metric_map (tag/unit, priority) -> period typing
               -> knowledge versions by filed date -> derived discrete quarters
               (Q2..Q4 = YTD - prior YTD, USD duration metrics only).
Supplemental:  stg_doc_line_item (current parse of parsed documents, not quarantined)
               -> label regex rules on the metric label (row label in series tables,
               column header in segment tables), filtered by column role and table
               kind -> versions by document publication date.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from ..core.db import Db, in_clause
from .periods import build_versions, classify_duration, fiscal_period, value_at

Q6 = Decimal("0.000001")
UNIT_FOR_TYPE = {
    "USD": "USD",
    "USD_PER_SHARE": "USD/shares",
    "PCT": "pct",
    "SF": "sf",
    "COUNT": "count",
    "SHARES": "shares",
    "RATIO": "x",
    "YEARS": "years",
    "USD_PER_SF": "USD/sf",
    "USD_PER_UNIT": "USD/unit",
    "MW": "MW",
}
HASH_FIELDS = (
    "company_id",
    "metric_id",
    "period_type",
    "period_start",
    "period_end",
    "fiscal_year",
    "fiscal_quarter",
    "segment",
    "value",
    "unit",
    "source_type",
    "version",
    "known_from",
    "known_to",
    "source_accession",
    "source_fact_id",
    "source_doc_id",
    "source_item_id",
    "source_page_no",
    "source_table_id",
    "source_sha256",
    "derivation",
)


def row_hash(r: dict[str, Any]) -> str:
    def norm(v: Any) -> Any:
        if isinstance(v, Decimal):
            return str(v.quantize(Q6))
        if isinstance(v, date):
            return v.isoformat()
        return v

    return hashlib.sha256(json.dumps([norm(r.get(k)) for k in HASH_FIELDS], separators=(",", ":")).encode()).hexdigest()


def _row(
    company: dict[str, Any],
    mid: int,
    ptype: str,
    start: date | None,
    end: date,
    seg: str,
    unit: str,
    source_type: str,
    v: dict[str, Any],
    **lineage: Any,
) -> dict[str, Any]:
    fy, fq = fiscal_period(end, company.get("fiscal_year_end"))
    r = {
        "company_id": company["company_id"],
        "metric_id": mid,
        "period_type": ptype,
        "period_start": start,
        "period_end": end,
        "fiscal_year": fy,
        "fiscal_quarter": None if ptype == "FY" else fq,
        "segment": seg,
        "value": v["value"],
        "unit": unit,
        "source_type": source_type,
        "version": v["version"],
        "known_from": v["known_from"],
        "known_to": v["known_to"],
        "source_accession": None,
        "source_fact_id": None,
        "source_doc_id": None,
        "source_item_id": None,
        "source_page_no": None,
        "source_table_id": None,
        "source_sha256": None,
        "derivation": None,
    }
    r.update(lineage)
    r["row_hash"] = row_hash(r)
    return r


# ------------------------------------------------------------------ XBRL
def desired_xbrl_rows(db: Db, company: dict[str, Any]) -> list[dict[str, Any]]:
    facts = db.query(
        """SELECT f.fact_id, f.period_start, f.period_end, f.duration_days, f.value, f.accession_no, f.filed, f.unit,
                  f.src_sha256, m.metric_id, m.priority, m.multiplier, d.period_nature, d.unit_type
           FROM stg_xbrl_fact f
           JOIN stg_xbrl_concept c ON c.concept_id = f.concept_id
           JOIN ref_metric_map m ON m.source_type='xbrl' AND m.is_active=1 AND m.taxonomy=c.taxonomy AND m.tag=c.tag
                AND (m.unit IS NULL OR m.unit=f.unit) AND (m.company_id IS NULL OR m.company_id=f.company_id)
                AND (m.valid_from IS NULL OR f.period_end >= m.valid_from)
                AND (m.valid_to IS NULL OR f.period_end <= m.valid_to)
           JOIN ref_metric d ON d.metric_id = m.metric_id AND d.is_active=1
           WHERE f.company_id=:c""",
        {"c": company["company_id"]},
    )
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    meta: dict[int, tuple[str, str]] = {}
    for f in facts:
        cls = classify_duration(f["duration_days"] if f["period_start"] else None)
        if cls is None or (cls[0] == "INSTANT") != (f["period_nature"] == "instant"):
            continue
        meta[f["metric_id"]] = (f["period_nature"], f["unit_type"])
        groups[(f["metric_id"], cls[0], f["period_start"], f["period_end"])].append(f)

    series: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for key, fs in groups.items():
        # Company-specific rules beat global ones; then lowest priority number wins.
        best = min(x["priority"] for x in fs)
        series[key] = build_versions(
            {
                "value": (x["value"] * x["multiplier"]).quantize(Q6),
                "as_of": x["filed"],
                "tiebreak": x["accession_no"],
                "fact_id": x["fact_id"],
                "accession": x["accession_no"],
                "unit": x["unit"],
                "sha": x["src_sha256"],
            }
            for x in fs
            if x["priority"] == best
        )

    out = []
    for (mid, ptype, start, end), versions in series.items():
        for v in versions:
            out.append(
                _row(
                    company,
                    mid,
                    ptype,
                    start,
                    end,
                    "Total",
                    v["unit"],
                    "xbrl",
                    v,
                    source_accession=v["accession"],
                    source_fact_id=v["fact_id"],
                    source_sha256=v["sha"],
                )
            )

    # Derived discrete quarters: Qk = YTD(k) - YTD(k-1) for additive USD flows, when not reported directly.
    cumulative: dict[tuple[int, date], dict[int, tuple[Any, ...]]] = defaultdict(dict)
    for key in series:
        mid, _ptype, start, end = key
        if meta[mid] != ("duration", "USD") or start is None:
            continue
        cls = classify_duration((end - start).days + 1)
        if cls:
            cumulative[(mid, start)][cls[1]] = key
    direct_q = {(k[0], k[3]): k[2] for k in series if k[1] == "Q"}
    for (mid, _start), cum in cumulative.items():
        for k in (2, 3, 4):
            if k not in cum or (k - 1) not in cum:
                continue
            prev_key, cur_key = cum[k - 1], cum[k]
            q_start, q_end = prev_key[3] + timedelta(days=1), cur_key[3]
            ds = direct_q.get((mid, q_end))
            if ds is not None and abs((ds - q_start).days) <= 3:
                continue
            a, b = series[cur_key], series[prev_key]
            obs = []
            for t in sorted({v["known_from"] for v in a} | {v["known_from"] for v in b}):
                va, vb = value_at(a, t), value_at(b, t)
                if va and vb:
                    obs.append(
                        {
                            "value": va["value"] - vb["value"],
                            "as_of": t,
                            "accession": va["accession"],
                            "unit": va["unit"],
                            "deriv": f"{cur_key[1]}{k} fact {va['fact_id']} minus "
                            f"{prev_key[1]}{k - 1} fact {vb['fact_id']}",
                        }
                    )
            for v in build_versions(obs):
                out.append(
                    _row(
                        company,
                        mid,
                        "Q",
                        q_start,
                        q_end,
                        "Total",
                        v["unit"],
                        "derived",
                        v,
                        source_accession=v["accession"],
                        derivation=v["deriv"][:255],
                    )
                )
    return out


# ------------------------------------------------------------------ supplementals
def desired_supplemental_rows(db: Db, company: dict[str, Any]) -> list[dict[str, Any]]:
    items = db.query(
        """SELECT i.item_id, i.table_id, i.page_no, i.value_numeric, i.segment_label, i.period_start, i.period_type,
                  COALESCE(i.period_end, t.as_of_date, d.period_end_date) AS period_end,
                  COALESCE(d.published_date, DATE(d.created_at)) AS as_of, d.doc_id, d.sha256, d.accession_no,
                  m.metric_id, m.priority, m.multiplier, md.period_nature, md.unit_type
           FROM stg_document d
           JOIN stg_doc_line_item i ON i.parse_id = d.current_parse_id AND i.quarantined = 0
           LEFT JOIN stg_doc_table t ON t.table_id = i.table_id
           JOIN ref_metric_map m ON m.source_type='supplemental' AND m.is_active=1
                AND (m.company_id IS NULL OR m.company_id = d.company_id)
                AND (m.col_role = 'any' OR m.col_role = i.col_role)
                AND REGEXP_LIKE(COALESCE(i.metric_label, i.row_label), m.label_regex, 'i')
                AND (m.exclude_regex IS NULL
                     OR NOT REGEXP_LIKE(COALESCE(i.metric_label, i.row_label), m.exclude_regex, 'i'))
                AND (m.segment_regex IS NULL OR REGEXP_LIKE(COALESCE(i.segment_label, ''), m.segment_regex, 'i'))
                AND (m.table_kind_regex IS NULL OR REGEXP_LIKE(COALESCE(t.table_kind, ''), m.table_kind_regex, 'i'))
                AND (m.valid_from IS NULL OR COALESCE(i.period_end, d.period_end_date) >= m.valid_from)
                AND (m.valid_to IS NULL OR COALESCE(i.period_end, d.period_end_date) <= m.valid_to)
           JOIN ref_metric md ON md.metric_id = m.metric_id AND md.is_active=1 AND md.grain <> 'property'
           WHERE d.company_id=:c AND d.status='parsed' AND i.value_numeric IS NOT NULL""",
        {"c": company["company_id"]},
    )
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for i in items:
        if i["period_end"] is None:
            continue
        if i["period_nature"] == "instant":
            ptype, start = "INSTANT", None
        else:
            start = i["period_start"]
            if start:
                cls = classify_duration((i["period_end"] - start).days + 1)
                if cls is None or cls[0] == "INSTANT":
                    if i["period_type"] != "TTM":
                        continue
                    cls = ("TTM", 4)
                ptype = "TTM" if i["period_type"] == "TTM" else cls[0]
            else:
                # no start read: trust the header's period type; a dated column of a duration metric is a quarter
                ptype = i["period_type"] if i["period_type"] in ("Q", "YTD", "FY", "TTM") else "Q"
        seg = (i["segment_label"] or "Total")[:128]
        groups[(i["metric_id"], ptype, start, i["period_end"], seg, i["unit_type"])].append(i)
    out = []
    for (mid, ptype, start, end, seg, utype), its in groups.items():
        best = min(x["priority"] for x in its)
        first_per_doc: dict[int, dict[str, Any]] = {}
        for x in sorted(its, key=lambda x: (x["doc_id"], x["page_no"], x["item_id"])):
            if x["priority"] == best:
                first_per_doc.setdefault(x["doc_id"], x)
        versions = build_versions(
            {
                **x,
                "value": (x["value_numeric"] * x["multiplier"]).quantize(Q6),
                "as_of": x["as_of"],
                "tiebreak": f"{x['doc_id']:012d}",
            }
            for x in first_per_doc.values()
        )
        for v in versions:
            out.append(
                _row(
                    company,
                    mid,
                    ptype,
                    start,
                    end,
                    seg,
                    UNIT_FOR_TYPE.get(utype, utype),
                    "supplemental",
                    v,
                    source_accession=v["accession_no"],
                    source_doc_id=v["doc_id"],
                    source_item_id=v["item_id"],
                    source_page_no=v["page_no"],
                    source_table_id=v["table_id"],
                    source_sha256=v["sha256"],
                )
            )
    return out


# ------------------------------------------------------------------ apply
INSERT_COLS = (*HASH_FIELDS, "row_hash", "recorded_run_id")


def apply_rows(
    db: Db, company_id: int, source_types: tuple[str, ...], desired: list[dict[str, Any]], run_id: int | None
) -> dict[str, int]:
    clause, params = in_clause("st", list(source_types))
    live = {
        r["row_hash"]: r["value_id"]
        for r in db.query(
            f"SELECT value_id, row_hash FROM wh_metric_value WHERE company_id=:c AND retired_at IS NULL "
            f"AND source_type IN {clause}",
            {"c": company_id, **params},
        )
    }
    want = {r["row_hash"]: r for r in desired}
    retire = [vid for h, vid in live.items() if h not in want]
    insert = [r for h, r in want.items() if h not in live]
    for i in range(0, len(retire), 1000):
        c2, p2 = in_clause("v", retire[i : i + 1000])
        db.execute(
            f"UPDATE wh_metric_value SET retired_at=CURRENT_TIMESTAMP(6), retired_run_id=:run WHERE value_id IN {c2}",
            {"run": run_id, **p2},
        )
    db.executemany(
        f"INSERT INTO wh_metric_value ({', '.join(INSERT_COLS)}) VALUES ({', '.join(':' + c for c in INSERT_COLS)})",
        ({**r, "recorded_run_id": run_id} for r in insert),
    )
    return {"retired": len(retire), "inserted": len(insert), "unchanged": len(live) - len(retire)}


def build_company(db: Db, company: dict[str, Any], run_id: int | None) -> dict[str, int]:
    """Rebuild all metric rows for one company in one transaction."""
    from .records import build_company_records

    with db.transaction():
        x = apply_rows(db, company["company_id"], ("xbrl", "derived"), desired_xbrl_rows(db, company), run_id)
        s = apply_rows(db, company["company_id"], ("supplemental",), desired_supplemental_rows(db, company), run_id)
        r = build_company_records(db, company["company_id"], run_id)
    return {k: x[k] + s[k] + r[k] for k in x}
