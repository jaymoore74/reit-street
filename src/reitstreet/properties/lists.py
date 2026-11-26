"""Property lists from company websites, public datasets and files people hand us.

    load_source   fetch one ref_property_source URL (robots.txt honored, rate limited,
                  raw store capture) and stage its rows
    import_file   stage a local CSV / XLSX / JSON / HTML file (an analyst list, an Excel
                  download, an archive) as manual or public_dataset records

Both go through listparse (read + map columns) and records.stage_records.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from ..core.db import Db
from ..core.http import Fetcher, register_object
from ..core.rawstore import RawStore
from . import normalize as nz
from .listparse import map_rows, read_rows
from .records import PropertyRecord, TypeClassifier, stage_records

log = logging.getLogger(__name__)
LIST_LOADER_VERSION = "1.0"
LOADABLE = ("html_table", "json", "csv", "xlsx")


def _unit_kinds(db: Db) -> dict[str, str]:
    return {
        r["type_code"]: r["default_unit_kind"]
        for r in db.query("SELECT type_code, default_unit_kind FROM ref_property_type")
    }


def to_records(
    mapped: list[dict[str, Any]],
    *,
    company_id: int,
    company_sector: str | None,
    source_kind: str,
    as_of: date,
    known_from: date,
    loader: str,
    source_url: str | None,
    source_sha256: str | None,
    source_row_id: int | None,
    classifier: TypeClassifier | None,
    unit_kinds: dict[str, str],
) -> list[PropertyRecord]:
    out = []
    for i, m in enumerate(mapped):
        m = dict(m)
        attrs = m.pop("attributes", {}) or {}
        acquired = m.pop("acquired_text", None)
        year = m.pop("year_built", None)
        type_code = (
            classifier.classify(m.get("raw_property_type"), m.get("name"), company_sector) if classifier else None
        )
        unit_kind = None
        if m.get("unit_count") is not None:
            unit_kind = unit_kinds.get(type_code or "", "units")
            if unit_kind == "sf":
                unit_kind = "units"
        loc = nz.Location(m.get("city"), m.get("state"))
        if not loc.city and m.get("market") and not m.get("state"):
            loc = nz.parse_location(m["market"])
        out.append(
            PropertyRecord(
                company_id=company_id,
                source_kind=source_kind,
                as_of_date=as_of,
                known_from=known_from,
                loader=loader,
                loader_version=LIST_LOADER_VERSION,
                source_row_id=source_row_id,
                source_row_index=i,
                source_url=source_url,
                source_sha256=source_sha256,
                source_property_ref=m.get("source_property_ref"),
                name=m.get("name"),
                address_line=m.get("address_line"),
                city=loc.city or m.get("city"),
                state=loc.state or m.get("state"),
                postal_code=m.get("postal_code"),
                market=m.get("market"),
                raw_property_type=m.get("raw_property_type"),
                type_code=type_code,
                rentable_sf=m.get("rentable_sf"),
                unit_count=m.get("unit_count"),
                unit_kind=unit_kind,
                year_built=nz.first_year(year),
                acquired_text=(acquired or None) and str(acquired)[:64],
                occupancy_pct=m.get("occupancy_pct"),
                ownership_pct=m.get("ownership_pct")
                if m.get("ownership_pct") is None or 0 <= m["ownership_pct"] <= 100
                else None,
                latitude=m.get("latitude"),
                longitude=m.get("longitude"),
                attributes={k: str(v)[:500] for k, v in list(attrs.items())[:40]},
            )
        )
    return out


def load_source(
    db: Db, fetcher: Fetcher, robots: Any, src: dict[str, Any], company: dict[str, Any], run_id: int | None
) -> dict[str, Any]:
    """Fetch and stage one source row. Returns counts; raises on fetch errors (job item fails)."""
    url = src["list_url"]
    if src["list_format"] not in LOADABLE:
        return {"skipped": f"format {src['list_format']} has no loader"}
    if robots is not None and not robots.allowed(url):
        db.execute(
            "UPDATE ref_property_source SET status='blocked' WHERE source_row_id=:s", {"s": src["source_row_id"]}
        )
        return {"skipped": "robots.txt disallows"}
    r = fetcher.get(url)
    assert r is not None
    rows = read_rows(r.body, src["list_format"], src.get("records_path"))
    fm = json.loads(src["field_map"]) if src.get("field_map") else None
    mapped, used = map_rows(rows, fm)
    today = date.today()
    recs = to_records(
        mapped,
        company_id=company["company_id"], company_sector=company.get("sector"), source_kind=src["source_kind"],
        as_of=today, known_from=today, loader=f"list_{src['list_format']}", source_url=url, source_sha256=r.sha256,
        source_row_id=src["source_row_id"], classifier=TypeClassifier.from_db(db), unit_kinds=_unit_kinds(db),
    )  # fmt: skip
    if not r.changed and db.scalar(
        "SELECT 1 FROM stg_property_record WHERE source_sha256=:s AND retired_at IS NULL LIMIT 1", {"s": r.sha256}
    ):
        return {"unchanged": True, "rows": len(rows)}
    stage_records(db, recs, run_id)
    db.commit()
    return {"rows": len(rows), "records": len(recs), "field_map": used}


def import_file(
    db: Db,
    store: RawStore,
    path: Path,
    *,
    fmt: str,
    as_of: date,
    known_from: date | None,
    source_kind: str,
    ticker: str | None,
    ticker_column: str | None,
    field_map: dict[str, str] | None,
    records_path: str | None,
    run_id: int | None,
) -> dict[str, Any]:
    body = path.read_bytes()
    sha, rel = store.put(body)
    register_object(db, sha, len(body), None, rel)
    rows = read_rows(body, fmt, records_path)
    companies = {
        r["ticker"]: dict(r)
        for r in db.query("SELECT company_id, ticker, sector FROM ref_company WHERE ticker IS NOT NULL")
    }
    groups: dict[str, list[dict[str, str]]] = {}
    for r in rows:
        t = (r.get(ticker_column) or "").strip().upper() if ticker_column else (ticker or "").upper()
        groups.setdefault(t, []).append(r)
    unknown = sorted(t for t in groups if t not in companies)
    staged = 0
    classifier = TypeClassifier.from_db(db)
    kinds = _unit_kinds(db)
    used: dict[str, str] = {}
    for t, rs in groups.items():
        c = companies.get(t)
        if c is None:
            continue
        mapped, used = map_rows(rs, field_map)
        recs = to_records(
            mapped, company_id=c["company_id"], company_sector=c.get("sector"), source_kind=source_kind,
            as_of=as_of, known_from=known_from or as_of, loader="import_file", source_url=f"file:{path.name}",
            source_sha256=sha, source_row_id=None, classifier=classifier, unit_kinds=kinds,
        )  # fmt: skip
        stage_records(db, recs, run_id)
        staged += len(recs)
    db.commit()
    return {"rows": len(rows), "records": staged, "unknown_tickers": unknown, "field_map": used}
