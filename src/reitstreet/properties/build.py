"""build-properties: place staged records on stable property ids and rebuild the master.

    1. links of records that were retired (reloaded with a better parser) are retired
    2. every live, non-aggregate record without a live link is matched (match.py)
    3. new properties get ids; links are written with method, score and review status
    4. for every property touched: canonical attributes (source priority, newest first),
       aliases, ownership spells (ownership.py), owner and status

Idempotent: a second run with nothing new changes nothing. `rematch=True` re-links every
automatic link (manual and confirmed links stay) after a matcher change; existing ids are
kept because the index carries every alias the old links taught it.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from datetime import date
from typing import Any

from ..core.db import Db, in_clause
from . import match as m
from .ownership import Sighting, compute_spells
from .records import SOURCE_PRIORITY

log = logging.getLogger(__name__)

CANON = [
    "name", "address_line", "city", "state", "postal_code", "country", "market", "type_code",
    "rentable_sf", "unit_count", "unit_kind", "land_acres", "year_built",
]  # fmt: skip


def snapshot_sql(a: str) -> str:
    """SQL for match.Rec.snapshot: the source locator (same order as PropertyRecord.locator) plus as-of date."""
    return (
        f"CONCAT(COALESCE({a}.source_sha256, {a}.source_url, CAST({a}.source_doc_id AS CHAR), {a}.accession_no, ''),"
        f" '@', {a}.as_of_date)"
    )


def _chunks(xs: list[Any], n: int = 1000) -> Any:
    for i in range(0, len(xs), n):
        yield xs[i : i + n]


def build_properties(
    db: Db, run_id: int | None, *, rematch: bool = False, companies: list[int] | None = None
) -> dict[str, int]:
    stats: dict[str, int] = defaultdict(int)
    # 1. links to retired records go
    touched: set[int] = {
        int(r["property_id"])
        for r in db.query(
            """SELECT DISTINCT x.property_id FROM wh_property_xref x
               JOIN stg_property_record r ON r.record_id = x.record_id
               WHERE x.retired_at IS NULL AND r.retired_at IS NOT NULL AND x.method <> 'manual'"""
        )
    }
    stats["links_retired"] = db.execute(
        """UPDATE wh_property_xref x JOIN stg_property_record r ON r.record_id = x.record_id
           SET x.retired_at = CURRENT_TIMESTAMP(6)
           WHERE x.retired_at IS NULL AND r.retired_at IS NOT NULL AND x.method <> 'manual'"""
    )
    if rematch:
        touched |= {
            int(r["property_id"])
            for r in db.query(
                """SELECT DISTINCT property_id FROM wh_property_xref WHERE retired_at IS NULL AND method <> 'manual'
                   AND review_status NOT IN ('confirmed','rejected')"""
            )
        }
        stats["links_rematched"] = db.execute(
            """UPDATE wh_property_xref SET retired_at = CURRENT_TIMESTAMP(6)
               WHERE retired_at IS NULL AND method <> 'manual' AND review_status NOT IN ('confirmed','rejected')"""
        )
    db.commit()

    # 2. what to place
    where = ""
    params: dict[str, Any] = {}
    if companies:
        clause, params = in_clause("c", companies)
        where = f" AND r.company_id IN {clause}"
    todo = db.query(
        f"""SELECT r.record_id, r.company_id, r.source_kind, {snapshot_sql("r")} AS snapshot,
                   r.source_property_ref, r.norm_name, r.address_key, r.city, r.state, r.latitude, r.longitude,
                   r.as_of_date
            FROM stg_property_record r
            LEFT JOIN wh_property_xref x ON x.record_id = r.record_id AND x.retired_at IS NULL
            WHERE r.retired_at IS NULL AND r.is_aggregate = 0 AND x.xref_id IS NULL{where}""",
        params,
    )
    todo.sort(key=lambda r: (SOURCE_PRIORITY.get(r["source_kind"], 9), r["as_of_date"], r["record_id"]))
    stats["records_to_place"] = len(todo)
    if todo:
        index = load_index(db)
        recs = [
            m.Rec(
                record_id=int(r["record_id"]),
                company_id=int(r["company_id"]),
                source_kind=r["source_kind"],
                snapshot=r["snapshot"],
                source_ref=r["source_property_ref"],
                norm_name=r["norm_name"],
                address_key=r["address_key"],
                city=r["city"],
                state=r["state"],
                latitude=float(r["latitude"]) if r["latitude"] is not None else None,
                longitude=float(r["longitude"]) if r["longitude"] is not None else None,
            )
            for r in todo
        ]
        decisions = m.place(index, recs)
        # 3. ids for new properties, then links
        real: dict[int, int] = {}
        for d in decisions:
            if d.new_key is not None and d.new_key not in real:
                real[d.new_key] = db.execute("INSERT INTO wh_property (created_run_id) VALUES (:r)", {"r": run_id})
                stats["properties_created"] += 1

        def resolve(pid: int) -> int:
            return real[pid] if pid < 0 else pid

        rows = []
        for d in decisions:
            pid = resolve(d.property_id) if d.property_id is not None else real[d.new_key]  # type: ignore[index]
            cands = [{**c, "property_id": resolve(c["property_id"])} for c in d.candidates]
            rows.append(
                {
                    "rec": d.record_id,
                    "pid": pid,
                    "meth": d.method,
                    "score": d.score if d.score is not None else 1.0,
                    "rev": d.review_status,
                    "cand": json.dumps(cands) if cands else None,
                    "ver": m.MATCHER_VERSION,
                    "run": run_id,
                }
            )
            touched.add(pid)
            stats[f"method_{d.method}"] += 1
            if d.review_status == "needs_review":
                stats["needs_review"] += 1
        db.executemany(
            """INSERT INTO wh_property_xref (record_id, property_id, method, score, review_status, candidates,
                 matcher_version, run_id) VALUES (:rec, :pid, :meth, :score, :rev, :cand, :ver, :run)""",
            rows,
        )
        db.commit()
    # 4. refresh what changed
    stats["properties_refreshed"] = refresh_properties(db, sorted(touched), run_id)
    db.commit()
    return dict(stats)


def load_index(db: Db) -> m.Index:
    idx = m.Index()
    owners: dict[int, set[int]] = defaultdict(set)
    snaps: dict[int, set[str]] = defaultdict(set)
    for r in db.query(
        f"""SELECT x.property_id, r.company_id, {snapshot_sql("r")} AS snapshot
            FROM wh_property_xref x JOIN stg_property_record r ON r.record_id = x.record_id
            WHERE x.retired_at IS NULL AND r.retired_at IS NULL"""
    ):
        owners[int(r["property_id"])].add(int(r["company_id"]))
        snaps[int(r["property_id"])].add(r["snapshot"])
    for p in db.query("SELECT property_id, city, state, latitude, longitude FROM wh_property WHERE status <> 'merged'"):
        pid = int(p["property_id"])
        idx.add_property(
            m.Prop(
                pid,
                companies=owners.get(pid, set()),
                city_key=m.city_key(p["city"], p["state"]),
                latitude=float(p["latitude"]) if p["latitude"] is not None else None,
                longitude=float(p["longitude"]) if p["longitude"] is not None else None,
                snapshots=snaps.get(pid, set()),
            )
        )
    for a in db.query(
        """SELECT a.property_id, a.alias_type, a.alias_norm, a.company_id, a.source_kind
           FROM wh_property_alias a JOIN wh_property p ON p.property_id = a.property_id AND p.status <> 'merged'"""
    ):
        idx.add_alias(int(a["property_id"]), a["alias_type"], a["alias_norm"], a["company_id"], a["source_kind"])
    return idx


def refresh_properties(db: Db, property_ids: list[int], run_id: int | None) -> int:
    """Recompute canonical attributes, aliases and ownership for these properties."""
    if not property_ids:
        return 0
    sizes: dict[int, dict[tuple[str, date], int]] = defaultdict(dict)
    changed = 0
    for chunk in _chunks(property_ids, 500):
        clause, params = in_clause("p", chunk)
        recs = db.query(
            f"""SELECT x.property_id, r.*, t.sector AS type_sector
                FROM wh_property_xref x
                JOIN stg_property_record r ON r.record_id = x.record_id AND r.retired_at IS NULL
                LEFT JOIN ref_property_type t ON t.type_code = r.type_code
                WHERE x.retired_at IS NULL AND x.property_id IN {clause}""",
            params,
        )
        by_prop: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for r in recs:
            by_prop[int(r["property_id"])].append(r)
        need = {int(r["company_id"]) for r in recs} - set(sizes)
        if need:
            cc, cp = in_clause("c", sorted(need))
            for s in db.query(
                f"""SELECT company_id, source_kind, as_of_date, COUNT(*) AS n FROM stg_property_record
                    WHERE retired_at IS NULL AND is_aggregate = 0 AND company_id IN {cc}
                    GROUP BY company_id, source_kind, as_of_date""",
                cp,
            ):
                sizes[int(s["company_id"])][(s["source_kind"], s["as_of_date"])] = int(s["n"])
        for pid in chunk:
            changed += _refresh_one(db, pid, by_prop.get(pid, []), sizes, run_id)
    return changed


def _canonical(records: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(
        records, key=lambda r: (SOURCE_PRIORITY.get(r["source_kind"], 9), -r["as_of_date"].toordinal(), -r["record_id"])
    )
    out: dict[str, Any] = {}
    for col in CANON:
        out[col] = next((r[col] for r in ordered if r[col] not in (None, "")), None)
    # size unit travels with its count
    for r in ordered:
        if r["unit_count"] is not None:
            out["unit_count"], out["unit_kind"] = r["unit_count"], r["unit_kind"]
            break
    out["sector"] = next(
        (r["type_sector"] for r in ordered if r["type_code"] == out["type_code"] and r["type_sector"]), None
    )
    geo = next((r for r in ordered if r["latitude"] is not None and r["longitude"] is not None), None)
    out["latitude"], out["longitude"] = (geo["latitude"], geo["longitude"]) if geo else (None, None)
    out["address_key"] = next((r["address_key"] for r in ordered if r["address_key"]), None)
    out["first_seen"] = min(r["as_of_date"] for r in records)
    out["last_seen"] = max(r["as_of_date"] for r in records)
    out["n_records"] = len(records)
    out["n_source_kinds"] = len({r["source_kind"] for r in records})
    return out


def _refresh_one(
    db: Db, pid: int, records: list[dict[str, Any]], sizes: dict[int, dict[tuple[str, date], int]], run_id: int | None
) -> int:
    if not records:
        # every record moved away (merge or re-match): keep the id, mark it inactive
        db.execute(
            """UPDATE wh_property SET status = IF(status='merged','merged','inactive'), n_records = 0
               WHERE property_id = :p""",
            {"p": pid},
        )
        _write_spells(db, pid, {}, run_id)
        return 1
    canon = _canonical(records)
    # aliases
    alias_rows = []
    for r in records:
        for kind, norm, raw in (
            ("name", r["norm_name"], r["name"]),
            ("address", r["address_key"], r["address_line"]),
            ("source_ref", r["source_property_ref"], r["source_property_ref"]),
        ):
            if norm:
                alias_rows.append(
                    {
                        "p": pid,
                        "t": kind,
                        "n": str(norm)[:255],
                        "v": str(raw or norm)[:512],
                        "c": r["company_id"],
                        "k": r["source_kind"],
                        "d": r["as_of_date"],
                    }
                )
    db.executemany(
        """INSERT INTO wh_property_alias (property_id, alias_type, alias_norm, alias_value, company_id, source_kind,
             first_seen, last_seen) VALUES (:p, :t, :n, :v, :c, :k, :d, :d) AS new
           ON DUPLICATE KEY UPDATE first_seen = LEAST(wh_property_alias.first_seen, new.first_seen),
             last_seen = GREATEST(wh_property_alias.last_seen, new.last_seen)""",
        alias_rows,
    )
    # ownership
    by_company: dict[int, list[Sighting]] = defaultdict(list)
    for r in records:
        by_company[int(r["company_id"])].append(
            Sighting(
                as_of=r["as_of_date"],
                source_kind=r["source_kind"],
                record_id=int(r["record_id"]),
                known_from=r["known_from"],
                ownership_pct=r["ownership_pct"],
                interest_type=r["interest_type"],
                acquired_date=r["acquired_date"],
                disposed_date=r["disposed_date"],
            )
        )
    spells = {c: compute_spells(s, sizes.get(c, {})) for c, s in by_company.items()}
    _write_spells(db, pid, spells, run_id)
    open_spells = [(c, sp[-1]) for c, sp in spells.items() if sp and sp[-1].disposed_date is None]
    owner = None
    if open_spells:
        owner = max(open_spells, key=lambda cs: (float(cs[1].ownership_pct or 100), cs[1].last_seen_as_of))[0]
    canon["owner_company_id"] = owner
    canon["status"] = "active" if owner else "inactive"
    canon["geo_precision"] = "reported" if canon["latitude"] is not None else "none"
    canon["geo_source"] = "source" if canon["latitude"] is not None else None
    # a geocode beats nothing; a reported point beats a geocode
    if canon["latitude"] is None and canon["address_key"]:
        g = db.one(
            """SELECT latitude, longitude, precision_level FROM ref_geocode_cache
               WHERE address_key = :k AND status = 'match'""",
            {"k": canon["address_key"]},
        )
        if g:
            canon.update(
                latitude=g["latitude"],
                longitude=g["longitude"],
                geo_precision=g["precision_level"],
                geo_source="census",
            )
    h = hashlib.sha256(json.dumps(canon, sort_keys=True, default=str).encode()).hexdigest()
    cols = list(canon)
    n = db.execute(
        f"""UPDATE wh_property SET {", ".join(f"{c} = :{c}" for c in cols)}, attr_hash = :h
            WHERE property_id = :pid AND status <> 'merged' AND (attr_hash IS NULL OR attr_hash <> :h)""",
        {**canon, "h": h, "pid": pid},
    )
    return 1 if n else 0


def _write_spells(db: Db, pid: int, spells: dict[int, list[Any]], run_id: int | None) -> None:
    live = {
        (int(r["company_id"]), int(r["spell_no"])): r
        for r in db.query(
            """SELECT ownership_id, company_id, spell_no, row_hash FROM wh_property_ownership
               WHERE property_id = :p AND retired_at IS NULL""",
            {"p": pid},
        )
    }
    want = {(c, s.spell_no): s for c, sp in spells.items() for s in sp}
    for key, row in live.items():
        s = want.get(key)
        if s is None or s.row_hash() != row["row_hash"]:
            db.execute(
                "UPDATE wh_property_ownership SET retired_at = CURRENT_TIMESTAMP(6) WHERE ownership_id = :o",
                {"o": row["ownership_id"]},
            )
    for (c, no), s in want.items():
        have = live.get((c, no))
        if have is not None and have["row_hash"] == s.row_hash():
            continue
        db.execute(
            """INSERT INTO wh_property_ownership (property_id, company_id, spell_no, ownership_pct, interest_type,
                 acquired_date, acquired_basis, disposed_date, disposed_basis, first_seen_as_of, last_seen_as_of,
                 known_from, last_record_id, row_hash, recorded_run_id)
               VALUES (:p, :c, :spell_no, :ownership_pct, :interest_type, :acquired_date, :acquired_basis,
                 :disposed_date, :disposed_basis, :first_seen_as_of, :last_seen_as_of, :known_from,
                 :last_record_id, :row_hash, :run)""",
            {"p": pid, "c": c, "run": run_id, **s.as_row()},
        )
