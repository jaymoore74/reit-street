"""Review queue for close matches, and manual fixes. Every action is a manual link,
which no rebuild ever touches."""

from __future__ import annotations

import json
from typing import Any

from ..core.db import Db
from .build import refresh_properties
from .match import MATCHER_VERSION


def queue(db: Db, limit: int = 50, ticker: str | None = None) -> list[dict[str, Any]]:
    sql = """SELECT x.record_id, x.property_id, x.score, x.candidates, x.matched_at, c.ticker,
                    r.name, r.address_line, r.city, r.state, r.source_kind, r.as_of_date
             FROM wh_property_xref x
             JOIN stg_property_record r ON r.record_id = x.record_id
             JOIN ref_company c ON c.company_id = r.company_id
             WHERE x.retired_at IS NULL AND x.review_status = 'needs_review'"""
    params: dict[str, Any] = {"n": limit}
    if ticker:
        sql += " AND c.ticker = :t"
        params["t"] = ticker
    rows = db.query(sql + " ORDER BY x.matched_at LIMIT :n", params)
    for r in rows:
        r["candidates"] = json.loads(r["candidates"]) if isinstance(r["candidates"], str) else r["candidates"]
    return rows


def _relink(db: Db, record_id: int, property_id: int, status: str, run_id: int | None) -> int | None:
    old = db.scalar(
        "SELECT property_id FROM wh_property_xref WHERE record_id = :r AND retired_at IS NULL", {"r": record_id}
    )
    db.execute(
        "UPDATE wh_property_xref SET retired_at = CURRENT_TIMESTAMP(6) WHERE record_id = :r AND retired_at IS NULL",
        {"r": record_id},
    )
    db.execute(
        """INSERT INTO wh_property_xref (record_id, property_id, method, score, review_status, matcher_version, run_id)
           VALUES (:r, :p, 'manual', 1, :s, :v, :run)""",
        {"r": record_id, "p": property_id, "s": status, "v": MATCHER_VERSION, "run": run_id},
    )
    return int(old) if old is not None else None


def _retire_if_empty(db: Db, pid: int, into: int) -> bool:
    if db.scalar("SELECT COUNT(*) FROM wh_property_xref WHERE property_id = :p AND retired_at IS NULL", {"p": pid}):
        return False
    db.execute(
        "UPDATE wh_property SET status = 'merged', merged_into_property_id = :i WHERE property_id = :p",
        {"p": pid, "i": into},
    )
    return True


def confirm(db: Db, record_id: int, property_id: int, run_id: int | None = None) -> dict[str, Any]:
    """The record is property_id. Its old (new, provisional) property is merged away if now empty."""
    if not db.scalar("SELECT 1 FROM wh_property WHERE property_id = :p AND status <> 'merged'", {"p": property_id}):
        raise ValueError(f"property {property_id} does not exist or was merged")
    old = _relink(db, record_id, property_id, "confirmed", run_id)
    merged = bool(old and old != property_id and _retire_if_empty(db, old, property_id))
    refresh_properties(db, [p for p in (old, property_id) if p], run_id)
    db.commit()
    return {"record_id": record_id, "property_id": property_id, "previous": old, "previous_merged": merged}


def reject(db: Db, record_id: int) -> dict[str, Any]:
    """The record is not any candidate: keep its own property."""
    n = db.execute(
        "UPDATE wh_property_xref SET review_status = 'rejected' WHERE record_id = :r AND retired_at IS NULL",
        {"r": record_id},
    )
    db.commit()
    return {"record_id": record_id, "updated": n}


def merge(db: Db, from_id: int, into_id: int, run_id: int | None = None) -> dict[str, Any]:
    """from_id and into_id are the same building: every record moves to into_id."""
    if from_id == into_id:
        raise ValueError("a property cannot be merged into itself")
    recs = [
        int(r["record_id"])
        for r in db.query(
            "SELECT record_id FROM wh_property_xref WHERE property_id = :p AND retired_at IS NULL", {"p": from_id}
        )
    ]
    for r in recs:
        _relink(db, r, into_id, "confirmed", run_id)
    db.execute(
        "UPDATE wh_property SET status = 'merged', merged_into_property_id = :i WHERE property_id = :p",
        {"p": from_id, "i": into_id},
    )
    db.execute(
        "UPDATE wh_property SET merged_into_property_id = :i WHERE merged_into_property_id = :p",
        {"p": from_id, "i": into_id},
    )
    refresh_properties(db, [from_id, into_id], run_id)
    db.commit()
    return {"merged": from_id, "into": into_id, "records_moved": len(recs)}
