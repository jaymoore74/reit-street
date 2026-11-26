"""Property source map: reference/data/property_sources.csv (versioned in git) -> ref_property_source.

One row per (company, source kind, URL): where a REIT's properties are listed, in what
format, and whether a loader runs it. Changed by pull request; synced by `reitstreet seed`.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import date
from importlib import resources
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..core.db import Db

FILE = "property_sources.csv"
KINDS = {"sec_schedule_iii", "supplemental", "corporate_website", "public_dataset", "manual"}
LISTS = {"yes", "partial", "no", "unknown"}
STATUSES = {"active", "candidate", "not_available", "blocked", "unknown"}
FORMATS = {"xbrl", "html_table", "html_cards", "json", "csv", "xlsx", "pdf", "map_only", "unknown"}


def _text() -> str:
    return resources.files("reitstreet.reference").joinpath("data", FILE).read_text(encoding="utf-8")


def read_sources(text: str | None = None) -> list[dict[str, Any]]:
    text = _text() if text is None else text
    out = []
    for i, r in enumerate(csv.DictReader(text.splitlines()), start=2):
        t = (r.get("ticker") or "").strip().upper()
        if not t:
            continue
        for col, allowed in (
            ("source_kind", KINDS),
            ("lists_properties", LISTS),
            ("status", STATUSES),
            ("list_format", FORMATS),
        ):
            if r[col] not in allowed:
                raise ValueError(f"{FILE} line {i}: {col} {r[col]!r} not in {sorted(allowed)}")
        fm = (r.get("field_map") or "").strip()
        if fm:
            json.loads(fm)  # must be valid JSON
        out.append(
            {
                "ticker": t,
                "source_kind": r["source_kind"],
                "lists_properties": r["lists_properties"],
                "status": r["status"],
                "list_url": (r.get("list_url") or "").strip() or None,
                "list_format": r["list_format"],
                "records_path": (r.get("records_path") or "").strip() or None,
                "field_map": fm or None,
                "detail": (r.get("detail") or "").strip() or None,
                "approx": int(r["approx_property_count"])
                if (r.get("approx_property_count") or "").strip().isdigit()
                else None,
                "count_unit": (r.get("count_unit") or "").strip()[:32] or None,
                "checked": date.fromisoformat(r["checked_date"]) if (r.get("checked_date") or "").strip() else None,
                "notes": (r.get("notes") or "").strip()[:1024] or None,
            }
        )
    return out


def apply_sources(db: Db, text: str | None = None) -> dict[str, int]:
    raw = _text() if text is None else text
    label = f"{FILE}@{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12]}"
    rows = read_sources(raw)
    ids = {
        r["ticker"]: r["company_id"]
        for r in db.query("SELECT company_id, ticker FROM ref_company WHERE ticker IS NOT NULL")
    }
    applied = 0
    for r in rows:
        cid = ids.get(r["ticker"])
        if cid is None:
            continue
        db.execute(
            """INSERT INTO ref_property_source (company_id, source_kind, lists_properties, status, list_url,
                 list_format, records_path, field_map, detail, approx_property_count, count_unit, checked_date, notes,
                 profile_source)
               VALUES (:c, :k, :lp, :st, :u, :f, :rp, :fm, :d, :n, :cu, :ck, :no, :src) AS new
               ON DUPLICATE KEY UPDATE lists_properties=new.lists_properties, status=new.status,
                 list_format=new.list_format, records_path=new.records_path, field_map=new.field_map,
                 detail=new.detail, approx_property_count=new.approx_property_count, count_unit=new.count_unit,
                 checked_date=new.checked_date, notes=new.notes, profile_source=new.profile_source""",
            {
                "c": cid,
                "k": r["source_kind"],
                "lp": r["lists_properties"],
                "st": r["status"],
                "u": r["list_url"],
                "f": r["list_format"],
                "rp": r["records_path"],
                "fm": r["field_map"],
                "d": r["detail"],
                "n": r["approx"],
                "cu": r["count_unit"],
                "ck": r["checked"],
                "no": r["notes"],
                "src": label,
            },
        )
        applied += 1
    db.commit()
    return {"property_sources_in_file": len(rows), "property_sources_applied": applied}
