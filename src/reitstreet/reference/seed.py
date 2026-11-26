"""Load the metric dictionary and mapping rules from the CSV files in reference/data.

Reference data lives in git (reviewable, diffable) and is synced into ref_metric and
ref_metric_map by `reitstreet migrate` or `reitstreet seed`. The sync is idempotent:
  * metrics upsert on metric_code; metrics dropped from the CSV become is_active=0
  * seed mapping rules upsert on map_key; rules dropped become is_active=0
  * manual rules (origin='manual', added by analysts in the database) are never touched
"""

from __future__ import annotations

import csv
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
            """INSERT INTO ref_metric (metric_code, name, category, unit_type, period_nature, is_non_gaap,
                                       is_nareit_defined, definition, is_active)
               VALUES (:code, :name, :cat, :ut, :pn, :ng, :nd, :df, 1) AS new
               ON DUPLICATE KEY UPDATE name=new.name, category=new.category, unit_type=new.unit_type,
                 period_nature=new.period_nature, is_non_gaap=new.is_non_gaap,
                 is_nareit_defined=new.is_nareit_defined, definition=new.definition, is_active=1""",
            {
                "code": m["metric_code"],
                "name": m["name"],
                "cat": m["category"],
                "ut": m["unit_type"],
                "pn": m["period_nature"],
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
            "pri": int(r["priority"] or 100),
            "mul": r["multiplier"] or "1",
            "notes": _none(r["notes"]),
        }
        db.execute(
            """INSERT INTO ref_metric_map (map_key, metric_id, source_type, taxonomy, tag, unit, label_regex,
                 exclude_regex, segment_regex, priority, multiplier, is_active, origin, notes)
               VALUES (:k, :mid, :st, :tax, :tag, :unit, :rx, :ex, :seg, :pri, :mul, 1, 'seed', :notes) AS new
               ON DUPLICATE KEY UPDATE metric_id=new.metric_id, source_type=new.source_type, taxonomy=new.taxonomy,
                 tag=new.tag, unit=new.unit, label_regex=new.label_regex, exclude_regex=new.exclude_regex,
                 segment_regex=new.segment_regex, priority=new.priority, multiplier=new.multiplier,
                 is_active=1, notes=new.notes""",
            p,
        )
    clause, params = in_clause("k", [r["map_key"] for r in rules])
    db.execute(f"UPDATE ref_metric_map SET is_active=0 WHERE origin='seed' AND map_key NOT IN {clause}", params)
    db.commit()
    return {"metrics": len(metrics), "rules": len(rules), **seed_property_types(db)}


def seed_property_types(db: Db) -> dict[str, int]:
    """property_types.csv -> ref_property_type; property_type_map.csv -> ref_property_type_map.
    Map rules dropped from the CSV are deleted (the table holds seed rules only)."""
    types = _read("property_types.csv")
    for t in types:
        db.execute(
            """INSERT INTO ref_property_type (type_code, sector, name, default_unit_kind, description)
               VALUES (:c, :s, :n, :u, :d) AS new
               ON DUPLICATE KEY UPDATE sector=new.sector, name=new.name, default_unit_kind=new.default_unit_kind,
                 description=new.description""",
            {
                "c": t["type_code"],
                "s": t["sector"],
                "n": t["name"],
                "u": t["default_unit_kind"],
                "d": _none(t["description"]),
            },
        )
    codes = {t["type_code"] for t in types}
    rules = _read("property_type_map.csv")
    for r in rules:
        if r["type_code"] not in codes:
            raise ValueError(f"property_type_map.csv: unknown type_code {r['type_code']} (key {r['rule_key']})")
        db.execute(
            """INSERT INTO ref_property_type_map (rule_key, pattern, type_code, priority, applies_to, notes)
               VALUES (:k, :p, :t, :pr, :a, :n) AS new
               ON DUPLICATE KEY UPDATE pattern=new.pattern, type_code=new.type_code, priority=new.priority,
                 applies_to=new.applies_to, notes=new.notes""",
            {
                "k": r["rule_key"],
                "p": r["pattern"],
                "t": r["type_code"],
                "pr": int(r["priority"]),
                "a": r["applies_to"],
                "n": _none(r["notes"]),
            },
        )
    clause, params = in_clause("k", [r["rule_key"] for r in rules])
    db.execute(f"DELETE FROM ref_property_type_map WHERE rule_key NOT IN {clause}", params)
    db.commit()
    return {"property_types": len(types), "property_type_rules": len(rules)}
