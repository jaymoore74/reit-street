"""Load reference data from the CSV files in reference/data.

Reference data lives in git (reviewable, diffable) and is synced into the ref_ tables by
`reitstreet migrate` or `reitstreet seed`. The sync is idempotent:
  * metrics upsert on metric_code; metrics dropped from the CSV become is_active=0
  * seed mapping rules upsert on map_key; rules dropped become is_active=0
  * sectors upsert on sector_code; the sector catalog (metric_sectors.csv) is replaced
  * company sectors come from each company's universe sector label (sector_labels.csv);
    seed rows are replaced, manual rows (origin='manual') are never touched
  * parse overrides upsert on override_key; seed rows dropped become is_active=0
  * manual rules and overrides (origin='manual', added by analysts) are never touched
"""

from __future__ import annotations

import csv
import json
from importlib import resources
from typing import Any

from ..core.db import Db, in_clause


def _read(name: str) -> list[dict[str, str]]:
    text = resources.files("reitstreet.reference").joinpath("data", name).read_text(encoding="utf-8")
    return list(csv.DictReader(text.splitlines()))


def _none(v: str | None) -> str | None:
    return v if v not in (None, "") else None


def seed_reference(db: Db) -> dict[str, int]:
    metrics = _read("metrics.csv")
    for m in metrics:
        db.execute(
            """INSERT INTO ref_metric (metric_code, name, category, unit_type, period_nature, grain, is_non_gaap,
                                       is_nareit_defined, definition, is_active)
               VALUES (:code, :name, :cat, :ut, :pn, :gr, :ng, :nd, :df, 1) AS new
               ON DUPLICATE KEY UPDATE name=new.name, category=new.category, unit_type=new.unit_type,
                 period_nature=new.period_nature, grain=new.grain, is_non_gaap=new.is_non_gaap,
                 is_nareit_defined=new.is_nareit_defined, definition=new.definition, is_active=1""",
            {
                "code": m["metric_code"],
                "name": m["name"],
                "cat": m["category"],
                "ut": m["unit_type"],
                "pn": m["period_nature"],
                "gr": m.get("grain") or "company",
                "ng": int(m["is_non_gaap"]),
                "nd": int(m["is_nareit_defined"]),
                "df": _none(m["definition"]),
            },
        )
    codes = [m["metric_code"] for m in metrics]
    clause, params = in_clause("c", codes)
    db.execute(f"UPDATE ref_metric SET is_active=0 WHERE metric_code NOT IN {clause}", params)
    ids = {r["metric_code"]: r["metric_id"] for r in db.query("SELECT metric_id, metric_code FROM ref_metric")}

    rules = _read("metric_map.csv")
    for r in rules:
        if r["metric_code"] not in ids:
            raise ValueError(f"metric_map.csv: unknown metric_code {r['metric_code']} (key {r['map_key']})")
        p: dict[str, Any] = {
            "k": r["map_key"],
            "mid": ids[r["metric_code"]],
            "st": r["source_type"],
            "tax": _none(r["taxonomy"]),
            "tag": _none(r["tag"]),
            "unit": _none(r["unit"]),
            "rx": _none(r["label_regex"]),
            "ex": _none(r["exclude_regex"]),
            "seg": _none(r["segment_regex"]),
            "role": r.get("col_role") or "value",
            "tk": _none(r.get("table_kind_regex")),
            "pri": int(r["priority"] or 100),
            "mul": r["multiplier"] or "1",
            "notes": _none(r["notes"]),
        }
        db.execute(
            """INSERT INTO ref_metric_map (map_key, metric_id, source_type, taxonomy, tag, unit, label_regex,
                 exclude_regex, segment_regex, col_role, table_kind_regex, priority, multiplier, is_active, origin,
                 notes)
               VALUES (:k, :mid, :st, :tax, :tag, :unit, :rx, :ex, :seg, :role, :tk, :pri, :mul, 1, 'seed', :notes)
                 AS new
               ON DUPLICATE KEY UPDATE metric_id=new.metric_id, source_type=new.source_type, taxonomy=new.taxonomy,
                 tag=new.tag, unit=new.unit, label_regex=new.label_regex, exclude_regex=new.exclude_regex,
                 segment_regex=new.segment_regex, col_role=new.col_role, table_kind_regex=new.table_kind_regex,
                 priority=new.priority, multiplier=new.multiplier, is_active=1, notes=new.notes""",
            p,
        )
    clause, params = in_clause("k", [r["map_key"] for r in rules])
    db.execute(f"UPDATE ref_metric_map SET is_active=0 WHERE origin='seed' AND map_key NOT IN {clause}", params)
    counts = {"metrics": len(metrics), "rules": len(rules)}
    counts.update(seed_sectors(db, ids))
    counts.update(seed_company_sectors(db))
    counts.update(seed_parse_overrides(db))
    db.commit()
    return counts


def seed_sectors(db: Db, metric_ids: dict[str, int]) -> dict[str, int]:
    sectors = _read("sectors.csv")
    # parents first so a child's parent always exists
    for s in sorted(sectors, key=lambda r: (r["parent_code"] not in ("all", ""), int(r["sort_order"] or 100))):
        db.execute(
            """INSERT INTO ref_sector (sector_code, name, parent_code, description, sort_order)
               VALUES (:c, :n, :p, :d, :o) AS new
               ON DUPLICATE KEY UPDATE name=new.name, parent_code=new.parent_code, description=new.description,
                 sort_order=new.sort_order""",
            {
                "c": s["sector_code"],
                "n": s["name"],
                "p": _none(s["parent_code"]),
                "d": _none(s["description"]),
                "o": int(s["sort_order"] or 100),
            },
        )
    codes = {s["sector_code"] for s in sectors}
    cat = _read("metric_sectors.csv")
    for r in cat:
        if r["metric_code"] not in metric_ids:
            raise ValueError(f"metric_sectors.csv: unknown metric_code {r['metric_code']}")
        if r["sector_code"] not in codes:
            raise ValueError(f"metric_sectors.csv: unknown sector_code {r['sector_code']}")
    db.execute("DELETE FROM ref_metric_sector")
    db.executemany(
        "INSERT INTO ref_metric_sector (metric_id, sector_code, relevance) VALUES (:m, :s, :r)",
        ({"m": metric_ids[r["metric_code"]], "s": r["sector_code"], "r": r["relevance"]} for r in cat),
    )
    return {"sectors": len(sectors), "sector_metrics": len(cat)}


def sector_label_map() -> dict[str, list[str]]:
    """Universe sector label (lower case) -> sector codes, primary first."""
    return {
        r["universe_label"].strip().lower(): [c.strip() for c in r["sector_codes"].split(";") if c.strip()]
        for r in _read("sector_labels.csv")
    }


def seed_company_sectors(db: Db) -> dict[str, int]:
    labels = sector_label_map()
    known = {r["sector_code"] for r in db.query("SELECT sector_code FROM ref_sector")}
    for codes in labels.values():
        for c in codes:
            if c not in known:
                raise ValueError(f"sector_labels.csv: unknown sector_code {c}")
    db.execute("DELETE FROM ref_company_sector WHERE origin='seed'")
    manual = {
        (r["company_id"], r["sector_code"]) for r in db.query("SELECT company_id, sector_code FROM ref_company_sector")
    }
    rows = []
    unmapped = 0
    for co in db.query("SELECT company_id, sector FROM ref_company WHERE sector IS NOT NULL"):
        mapped = labels.get(str(co["sector"]).strip().lower())
        if not mapped:
            unmapped += 1
            continue
        for i, code in enumerate(mapped):
            if (co["company_id"], code) not in manual:
                rows.append({"c": co["company_id"], "s": code, "p": int(i == 0)})
    db.executemany(
        "INSERT INTO ref_company_sector (company_id, sector_code, is_primary, origin) VALUES (:c, :s, :p, 'seed')", rows
    )
    return {"company_sectors": len(rows), "companies_without_sector_mapping": unmapped}


def seed_parse_overrides(db: Db) -> dict[str, int]:
    rows = _read("parse_overrides.csv")
    tickers = {
        str(r["ticker"]).upper(): r["company_id"] for r in db.query("SELECT company_id, ticker FROM ref_company")
    }
    n = 0
    for r in rows:
        cid = tickers.get(r["ticker"].strip().upper())
        if cid is None:
            continue  # company not loaded yet; applied on the next seed after the universe load
        params = _none(r["params"])
        if params is not None:
            json.loads(params)  # fail loudly on bad JSON in the seed file
        db.execute(
            """INSERT INTO ref_parse_override (override_key, company_id, action, title_regex, page_from, page_to,
                 params, valid_from, valid_to, is_active, origin, notes)
               VALUES (:k, :c, :a, :t, :pf, :pt, :pa, :vf, :vt, 1, 'seed', :n) AS new
               ON DUPLICATE KEY UPDATE company_id=new.company_id, action=new.action, title_regex=new.title_regex,
                 page_from=new.page_from, page_to=new.page_to, params=new.params, valid_from=new.valid_from,
                 valid_to=new.valid_to, is_active=1, notes=new.notes""",
            {
                "k": r["override_key"],
                "c": cid,
                "a": r["action"],
                "t": _none(r["title_regex"]),
                "pf": _none(r["page_from"]),
                "pt": _none(r["page_to"]),
                "pa": params,
                "vf": _none(r["valid_from"]),
                "vt": _none(r["valid_to"]),
                "n": _none(r["notes"]),
            },
        )
        n += 1
    keys = [r["override_key"] for r in rows] or ["-"]
    clause, params2 = in_clause("k", keys)
    db.execute(
        f"UPDATE ref_parse_override SET is_active=0 WHERE origin='seed' AND override_key NOT IN {clause}", params2
    )
    return {"parse_overrides": n}
