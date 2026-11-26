"""Build the record warehouse tables from stg_doc_record. Bitemporal and idempotent.

One builder per record kind turns the typed fields of a parsed table row into rows of

  wh_property_metric      one row per property, metric and date (PROP_* metrics)
  wh_lease_expiration     lease expiration schedule buckets
  wh_tenant_exposure      top tenant / customer / operator lists
  wh_development_project  development and redevelopment pipelines
  wh_debt_instrument      individual loans and notes
  wh_debt_maturity        debt maturity ladders (one row per bucket and debt type)

The same rules as wh_metric_value apply (see metrics.py): every row carries valid time
(as_of_date), knowledge time (known_from / known_to, from the publication date of the
source document) and system time (recorded_at / retired_at). The build computes the
full desired set of rows for a company from the current parse of each document and
diffs it against the live rows by row_hash, so a rebuild with no upstream change writes
nothing and a parser fix retires the old rows instead of deleting them.

Quarantined records never reach these tables. Each property row is also staged in
stg_property_record (source_kind 'supplemental') through the property master's
stage_records, and wh_property_metric keys to that record_id; the stable property_id
comes through wh_property_xref once build-properties has matched it (ADR 0010, 0011).
This module never writes wh_property.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from ..core.db import Db, in_clause
from ..properties.records import PropertyRecord, stage_records
from .metrics import UNIT_FOR_TYPE
from .periods import build_versions

# record field -> per-property metric code
PROPERTY_FIELDS = {
    "sf": "PROP_SF",
    "units": "PROP_UNITS",
    "beds": "PROP_UNITS",
    "rooms": "PROP_ROOMS",
    "occupancy": "PROP_OCCUPANCY",
    "leased_pct": "PROP_LEASED_PCT",
    "abr": "PROP_ABR",
    "abr_psf": "PROP_ABR_PSF",
    "noi": "PROP_NOI",
    "ebitda": "PROP_EBITDA",
    "adr": "PROP_ADR",
    "revpar": "PROP_REVPAR",
    "ownership_pct": "PROP_OWNERSHIP_PCT",
    "debt": "PROP_DEBT",
}
LINEAGE_COLS = (
    "known_from",
    "known_to",
    "version",
    "confidence",
    "source_doc_id",
    "source_sha256",
    "source_page_no",
    "source_table_id",
    "source_record_id",
)
YEAR_RX = re.compile(r"\b(19|20)\d\d\b")
TOTAL_TYPE_RX = re.compile(r"^(total|amount|principal|total debt|balance|outstanding)\b", re.I)


@dataclass(frozen=True)
class Target:
    table: str
    id_col: str
    kinds: tuple[str, ...]
    key: tuple[str, ...]  # natural key within a company: one version chain per key
    values: tuple[str, ...]  # columns that, when changed by a later document, start a new version
    links: tuple[str, ...] = ()  # stored with the version's first observation, never start a new version


TARGETS = (
    Target(
        "wh_property_metric",
        "property_metric_id",
        ("property",),
        ("property_key", "metric_id", "as_of_date"),
        ("value", "unit", "property_name", "city", "state", "market", "property_type"),
        ("property_record_id",),
    ),
    Target(
        "wh_lease_expiration",
        "lease_exp_id",
        ("lease_expiration",),
        ("as_of_date", "segment", "bucket_label"),
        (
            "expiry_year",
            "n_leases",
            "n_properties",
            "square_feet",
            "annualized_rent",
            "rent_pct",
            "rent_psf",
            "is_total",
        ),
    ),
    Target(
        "wh_tenant_exposure",
        "tenant_exp_id",
        ("tenant",),
        ("as_of_date", "tenant_name"),
        (
            "tenant_rank",
            "industry",
            "credit_rating",
            "n_properties",
            "n_leases",
            "square_feet",
            "annualized_rent",
            "rent_pct",
            "wale_years",
            "is_total",
        ),
    ),
    Target(
        "wh_development_project",
        "dev_project_id",
        ("development",),
        ("as_of_date", "segment", "project_name"),
        (
            "market",
            "property_type",
            "units",
            "square_feet",
            "total_cost",
            "cost_to_date",
            "remaining_cost",
            "pct_leased",
            "stabilized_yield",
            "ownership_pct",
            "completion_text",
            "is_total",
        ),
    ),
    Target(
        "wh_debt_instrument",
        "instrument_id",
        ("debt_detail",),
        ("as_of_date", "segment", "instrument"),
        ("debt_type", "principal", "rate", "effective_rate", "maturity_text", "maturity_year", "is_total"),
    ),
    Target(
        "wh_debt_maturity",
        "maturity_id",
        ("debt_maturity",),
        ("as_of_date", "bucket_label", "debt_type"),
        ("maturity_year", "amount", "wtd_avg_rate", "pct_of_total", "is_total"),
    ),
)


# ------------------------------------------------------------------ helpers
def dec(v: Any) -> Decimal | None:
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def whole(v: Any) -> int | None:
    d = dec(v)
    return int(d) if d is not None else None


def text(v: Any, n: int) -> str | None:
    if v is None or isinstance(v, dict | list):
        return None
    s = str(v).strip()
    return s[:n] if s else None


def year_of(label: str | None) -> int | None:
    m = YEAR_RX.search(label or "")
    return int(m.group(0)) if m else None


def norm_name(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def property_key(name: str, city: str | None, state: str | None) -> str:
    """Hash of normalized name, city and state: equal for the same property across documents."""
    return hashlib.sha256("|".join([norm_name(name), norm_name(city), norm_name(state)]).encode()).hexdigest()


def row_hash(r: dict[str, Any]) -> str:
    def norm(v: Any) -> Any:
        if isinstance(v, Decimal):
            return format(v.normalize(), "f")
        if isinstance(v, date):
            return v.isoformat()
        if isinstance(v, dict):
            return {str(k): norm(x) for k, x in sorted(v.items())}
        if isinstance(v, list | tuple):
            return [norm(x) for x in v]
        return v

    skip = {"row_hash", "recorded_run_id"}
    return hashlib.sha256(
        json.dumps({k: norm(v) for k, v in sorted(r.items()) if k not in skip}, separators=(",", ":")).encode()
    ).hexdigest()


# ------------------------------------------------------------------ record -> rows
Rows = list[dict[str, Any]]


def _lease(rec: dict[str, Any], f: dict[str, Any], ctx: dict[str, Any]) -> Rows:
    label = rec["label"]
    return [
        {
            "bucket_label": label[:64],
            "segment": (text(f.get("group"), 128) or "Total"),
            "expiry_year": year_of(label),
            "n_leases": whole(f.get("n_leases")),
            "n_properties": whole(f.get("n_properties")),
            "square_feet": dec(f.get("sf")),
            "annualized_rent": dec(f.get("abr")),
            "rent_pct": dec(f.get("abr_pct")),
            "rent_psf": dec(f.get("abr_psf")),
            "is_total": int(bool(f.get("is_total"))),
        }
    ]


def _tenant(rec: dict[str, Any], f: dict[str, Any], ctx: dict[str, Any]) -> Rows:
    return [
        {
            "tenant_name": rec["label"][:255],
            "tenant_rank": whole(f.get("rank")),
            "industry": text(f.get("industry"), 128),
            "credit_rating": text(f.get("credit_rating"), 32),
            "n_properties": whole(f.get("n_properties")),
            "n_leases": whole(f.get("n_leases")),
            "square_feet": dec(f.get("sf")),
            "annualized_rent": dec(f.get("abr")),
            "rent_pct": dec(f.get("abr_pct")),
            "wale_years": dec(f.get("wale")),
            "is_total": int(bool(f.get("is_total"))),
        }
    ]


def _development(rec: dict[str, Any], f: dict[str, Any], ctx: dict[str, Any]) -> Rows:
    return [
        {
            "project_name": rec["label"][:255],
            "segment": (text(f.get("group"), 128) or "Total"),
            "market": text(f.get("market"), 128),
            "property_type": text(f.get("property_type"), 64),
            "units": whole(f.get("units")),
            "square_feet": dec(f.get("sf")),
            "total_cost": dec(f.get("total_cost")),
            "cost_to_date": dec(f.get("cost_to_date")),
            "remaining_cost": dec(f.get("remaining_cost")),
            "pct_leased": dec(f.get("pct_leased")),
            "stabilized_yield": dec(f.get("stabilized_yield")),
            "ownership_pct": dec(f.get("ownership_pct")),
            "completion_text": text(f.get("completion"), 64),
            "is_total": int(bool(f.get("is_total"))),
        }
    ]


def _instrument(rec: dict[str, Any], f: dict[str, Any], ctx: dict[str, Any]) -> Rows:
    maturity = text(f.get("maturity"), 64)
    return [
        {
            "instrument": rec["label"][:255],
            "segment": (text(f.get("group"), 128) or "Total"),
            "debt_type": text(f.get("debt_type"), 64),
            "principal": dec(f.get("principal")),
            "rate": dec(f.get("rate")),
            "effective_rate": dec(f.get("effective_rate")),
            "maturity_text": maturity,
            "maturity_year": year_of(maturity) or year_of(rec["label"]),
            "is_total": int(bool(f.get("is_total"))),
        }
    ]


def _maturity(rec: dict[str, Any], f: dict[str, Any], ctx: dict[str, Any]) -> Rows:
    label = rec["label"]
    amounts = f.get("amounts") or {}
    out: Rows = []
    for name, v in amounts.items():
        amt = dec(v)
        if amt is None:
            continue
        debt_type = "Total" if len(amounts) == 1 or TOTAL_TYPE_RX.match(name) else name[:64]
        out.append(
            {
                "bucket_label": label[:64],
                "debt_type": debt_type,
                "maturity_year": year_of(label),
                "amount": amt,
                "wtd_avg_rate": dec(f.get("wtd_avg_rate")),
                "pct_of_total": dec(f.get("pct_of_total")),
                "is_total": int(bool(f.get("is_total")) or label.lower().startswith("total")),
            }
        )
    # two columns both read as Total (e.g. "Amount" and "Total"): keep the last, it is the right-most total
    seen: dict[str, dict[str, Any]] = {}
    for r in out:
        seen[r["debt_type"]] = r
    return list(seen.values())


def _property(rec: dict[str, Any], f: dict[str, Any], ctx: dict[str, Any]) -> Rows:
    if f.get("is_total"):
        return []
    name = rec["label"][:255]
    city, state = text(f.get("city"), 128), text(f.get("state"), 64)
    key = property_key(name, city, state)
    base = {
        "property_key": key,
        "property_record_id": ctx["property_records"].get(rec["record_id"]),
        "property_name": name,
        "city": city,
        "state": state,
        "market": text(f.get("market"), 128),
        "property_type": text(f.get("property_type"), 64),
    }
    out: Rows = []
    for field_name, code in PROPERTY_FIELDS.items():
        v = dec(f.get(field_name))
        m = ctx["metrics"].get(code)
        if v is None or m is None:
            continue
        out.append({**base, "metric_id": m["metric_id"], "value": v, "unit": UNIT_FOR_TYPE.get(m["unit_type"], "")})
    # beds and units both map to PROP_UNITS; keep one
    uniq: dict[tuple[Any, ...], dict[str, Any]] = {}
    for r in out:
        uniq.setdefault((r["metric_id"],), r)
    return list(uniq.values())


MAPPERS: dict[str, Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], Rows]] = {
    "lease_expiration": _lease,
    "tenant": _tenant,
    "development": _development,
    "debt_detail": _instrument,
    "debt_maturity": _maturity,
    "property": _property,
}


# ------------------------------------------------------------------ build
def _records(db: Db, company_id: int) -> list[dict[str, Any]]:
    return db.query(
        """SELECT r.record_id, r.parse_id, r.table_id, r.page_no, r.row_index, r.record_kind, r.label, r.fields,
                  r.confidence, COALESCE(r.as_of_date, d.period_end_date) AS as_of_date,
                  COALESCE(d.published_date, DATE(d.created_at)) AS published, d.doc_id, d.sha256
           FROM stg_document d
           JOIN stg_doc_record r ON r.parse_id = d.current_parse_id AND r.quarantined = 0
           WHERE d.company_id = :c AND d.status = 'parsed'""",
        {"c": company_id},
    )


def desired_rows(db: Db, company_id: int) -> dict[str, Rows]:
    """{table: desired rows} for one company, a pure function of staging and reference data."""
    ctx = {
        "metrics": {
            r["metric_code"]: r
            for r in db.query("SELECT metric_id, metric_code, unit_type FROM ref_metric WHERE grain='property'")
        },
        "property_records": {
            int(r["source_record_id"]): int(r["record_id"])
            for r in db.query(
                """SELECT p.record_id, CAST(JSON_UNQUOTE(JSON_EXTRACT(p.attributes, '$.source_record_id')) AS UNSIGNED)
                          AS source_record_id
                   FROM stg_property_record p
                   WHERE p.company_id = :c AND p.source_kind = 'supplemental' AND p.retired_at IS NULL
                     AND JSON_EXTRACT(p.attributes, '$.source_record_id') IS NOT NULL""",
                {"c": company_id},
            )
        },
    }
    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in _records(db, company_id):
        if rec["as_of_date"] is None:
            continue
        by_kind[rec["record_kind"]].append(rec)
    out: dict[str, Rows] = {}
    for t in TARGETS:
        chains: dict[tuple[Any, ...], dict[int, dict[str, Any]]] = defaultdict(dict)
        recs = [r for k in t.kinds for r in by_kind.get(k, [])]
        for rec in sorted(recs, key=lambda r: (r["doc_id"], r["page_no"], r["table_id"] or 0, r["row_index"] or 0)):
            f = rec["fields"]
            if isinstance(f, str | bytes):
                f = json.loads(f)
            for row in MAPPERS[rec["record_kind"]](rec, f, ctx):
                row["as_of_date"] = rec["as_of_date"]
                key = tuple(row[k] for k in t.key)
                # one observation per key per document: the first one in page order
                chains[key].setdefault(
                    rec["doc_id"],
                    {
                        "row": row,  # kept apart: wh_property_metric has a column named value
                        "value": tuple(row.get(c) for c in t.values),
                        "as_of": rec["published"],
                        "tiebreak": f"{rec['doc_id']:012d}",
                        "rec": rec,
                    },
                )
        rows: Rows = []
        for obs in chains.values():
            for v in build_versions(obs.values()):
                rec = v["rec"]
                r = {k: v["row"].get(k) for k in (*t.key, *t.values, *t.links)}
                r.update(
                    company_id=company_id,
                    known_from=v["known_from"],
                    known_to=v["known_to"],
                    version=v["version"],
                    confidence=rec["confidence"],
                    source_doc_id=rec["doc_id"],
                    source_sha256=rec["sha256"],
                    source_page_no=rec["page_no"],
                    source_table_id=rec["table_id"],
                    source_record_id=rec["record_id"],
                )
                r["row_hash"] = row_hash(r)
                rows.append(r)
        out[t.table] = rows
    return out


def apply_target(db: Db, t: Target, company_id: int, desired: Rows, run_id: int | None) -> dict[str, int]:
    live = {
        r["row_hash"]: r[t.id_col]
        for r in db.query(
            f"SELECT {t.id_col}, row_hash FROM {t.table} WHERE company_id=:c AND retired_at IS NULL", {"c": company_id}
        )
    }
    want = {r["row_hash"]: r for r in desired}
    retire = [i for h, i in live.items() if h not in want]
    insert = [r for h, r in want.items() if h not in live]
    for i in range(0, len(retire), 1000):
        c2, p2 = in_clause("v", retire[i : i + 1000])
        db.execute(
            f"UPDATE {t.table} SET retired_at=CURRENT_TIMESTAMP(6), retired_run_id=:run WHERE {t.id_col} IN {c2}",
            {"run": run_id, **p2},
        )
    if insert:
        cols = ("company_id", *t.key, *t.values, *t.links, *LINEAGE_COLS, "row_hash", "recorded_run_id")
        db.executemany(
            f"INSERT INTO {t.table} ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})",
            ({**r, "recorded_run_id": run_id} for r in insert),
        )
    return {"retired": len(retire), "inserted": len(insert), "unchanged": len(live) - len(retire)}


PROPERTY_LOADER = "supplemental_pdf"
PROPERTY_LOADER_VERSION = "1.0"


def property_records(db: Db, company_id: int) -> list[PropertyRecord]:
    """Each property row of the company's current supplemental parses, as a property master record.

    The parser owns what the row says; the property master (ADR 0010) owns which property it is.
    attributes.source_record_id ties the staged row back to stg_doc_record."""
    out: list[PropertyRecord] = []
    for rec in _records(db, company_id):
        if rec["record_kind"] != "property" or rec["as_of_date"] is None:
            continue
        f = rec["fields"]
        if isinstance(f, str | bytes):
            f = json.loads(f)
        if f.get("is_total") or not (rec["label"] or "").strip():
            continue
        units, beds, rooms = dec(f.get("units")), dec(f.get("beds")), dec(f.get("rooms"))
        unit_count, unit_kind = next(
            ((v, k) for v, k in ((units, "units"), (beds, "beds"), (rooms, "keys")) if v), (None, None)
        )
        year = year_of(text(f.get("year_built"), 32))
        own = dec(f.get("ownership_pct"))
        occ = dec(f.get("occupancy"))
        out.append(
            PropertyRecord(
                company_id=company_id,
                source_kind="supplemental",
                as_of_date=rec["as_of_date"],
                known_from=rec["published"],
                loader=PROPERTY_LOADER,
                loader_version=PROPERTY_LOADER_VERSION,
                source_doc_id=rec["doc_id"],
                source_parse_id=rec["parse_id"],
                source_table_id=rec["table_id"],
                source_page_no=rec["page_no"],
                source_row_index=(rec["row_index"] or 0) + 1,
                source_sha256=rec["sha256"],
                name=rec["label"][:255],
                city=text(f.get("city"), 128),
                state=text(f.get("state"), 64),
                market=text(f.get("market"), 128),
                raw_property_type=text(f.get("property_type"), 128),
                rentable_sf=dec(f.get("sf")),
                unit_count=unit_count,
                unit_kind=unit_kind,
                year_built=year if year and 1700 < year < 2100 else None,
                ownership_pct=own if own is not None and 0 <= own <= 100 else None,
                occupancy_pct=occ if occ is not None and 0 <= occ <= 100 else None,
                attributes={"source_record_id": int(rec["record_id"])},
            )
        )
    return out


def stage_properties(db: Db, company_id: int, run_id: int | None) -> int:
    """Stage the company's supplemental property rows in stg_property_record (idempotent)."""
    recs = property_records(db, company_id)
    return len(stage_records(db, recs, run_id)) if recs else 0


def build_company_records(db: Db, company_id: int, run_id: int | None) -> dict[str, int]:
    """Rebuild every record table for one company. Call inside a transaction."""
    stage_properties(db, company_id, run_id)
    desired = desired_rows(db, company_id)
    total = {"retired": 0, "inserted": 0, "unchanged": 0}
    for t in TARGETS:
        res = apply_target(db, t, company_id, desired[t.table], run_id)
        for k in total:
            total[k] += res[k]
    return total


def targets_for(kinds: Iterable[str]) -> list[Target]:
    ks = set(kinds)
    return [t for t in TARGETS if ks & set(t.kinds)]
