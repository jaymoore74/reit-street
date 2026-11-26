"""Generate the data dictionary (docs/data-dictionary.md) from information_schema, so the
documentation is the database: every table, view and column comment is written in the
migrations and a test fails if one is missing."""

from __future__ import annotations

from .db import Db

LAYERS = [
    ("ops_", "Operations: runs, checkpoints, dead letters, data quality, alerts"),
    ("raw_", "Raw layer: every byte received, content-addressed, immutable"),
    ("ref_", "Reference: companies, IR sources, metric dictionary and mapping rules"),
    ("stg_", "Staging: parsed source records, one row per source fact, re-derivable from raw"),
    ("wh_", "Warehouse: curated, bitemporal, lineage on every value"),
    ("dl_", "Delivery: client contract views (changing them is a breaking change)"),
]


def _md(s: str | None) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")


def data_dictionary(db: Db) -> str:
    rev = db.scalar("SELECT version_num FROM alembic_version")
    tables = db.query(
        """SELECT table_name AS name, table_type AS type, table_comment AS comment, table_rows AS approx_rows
           FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name <> 'alembic_version'
           ORDER BY table_name"""
    )
    cols = db.query(
        """SELECT table_name AS t, column_name AS c, column_type AS type, is_nullable AS n, column_key AS k,
                  extra AS x, column_comment AS comment
           FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name <> 'alembic_version'
           ORDER BY table_name, ordinal_position"""
    )
    by_table: dict[str, list[dict[str, str]]] = {}
    for c in cols:
        by_table.setdefault(c["t"], []).append(c)
    out = [
        "# Data dictionary",
        "",
        f"Generated from the database schema at Alembic revision `{rev}` by `reitstreet db-docs`. "
        "Do not edit by hand: change the COMMENT in a migration and regenerate.",
        "",
    ]
    for prefix, title in LAYERS:
        group = [t for t in tables if t["name"].startswith(prefix)]
        if not group:
            continue
        out += [f"## {prefix}* {title}", ""]
        out += [
            f"- [`{t['name']}`](#{t['name']}){' (view)' if t['type'] == 'VIEW' else ''}: {_md(t['comment'])}"
            for t in group
        ]
        out.append("")
        for t in group:
            kind = "View" if t["type"] == "VIEW" else "Table"
            out += [
                f"### {t['name']}",
                "",
                f"{kind}. {_md(t['comment']) if t['type'] != 'VIEW' else ''}".rstrip(),
                "",
                "| Column | Type | Null | Key | Description |",
                "|---|---|---|---|---|",
            ]
            for c in by_table.get(t["name"], []):
                x = (c["x"] or "").upper()
                gen = " (generated)" if ("VIRTUAL GENERATED" in x or "STORED GENERATED" in x) else ""
                out.append(f"| `{c['c']}` | {c['type']}{gen} | {c['n']} | {c['k'] or ''} | {_md(c['comment'])} |")
            out.append("")
    return "\n".join(out)
