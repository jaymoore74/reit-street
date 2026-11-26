"""Rows of property, tenant, development, debt and lease-expiration tables as typed records.

Column headers map to fields by the patterns in reference/data/record_fields.csv
(first match in file order wins, each field used once). Unmapped columns are kept
under `extra` with their header, so nothing printed is lost and a new pattern can be
added later and the stored parse rebuilt.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from importlib import resources
from typing import Any

from .classify import clean_label, is_yearish
from .layout import Table
from .values import Scale, apply_scale, column_scale, read_number


@dataclass(frozen=True)
class FieldRule:
    kind: str
    field: str
    rx: re.Pattern[str]
    value_type: str


@lru_cache(maxsize=1)
def field_rules() -> dict[str, tuple[FieldRule, ...]]:
    text = resources.files("reitstreet.reference").joinpath("data/record_fields.csv").read_text(encoding="utf-8")
    out: dict[str, list[FieldRule]] = {}
    for r in csv.DictReader(text.splitlines()):
        out.setdefault(r["record_kind"], []).append(
            FieldRule(r["record_kind"], r["field"], re.compile(r["header_regex"], re.I), r["value_type"])
        )
    return {k: tuple(v) for k, v in out.items()}


def map_columns(kind: str, headers: list[str]) -> dict[int, FieldRule | None]:
    """Column index (1-based) -> field rule, or None when no pattern fits."""
    rules = field_rules().get(kind, ())
    used: set[str] = set()
    out: dict[int, FieldRule | None] = {}
    for idx, h in enumerate(headers, start=1):
        h2 = clean_label(h)
        hit = None
        for r in rules:
            multi = r.field == "amount"  # debt maturity: every money column is an amount by type
            if (multi or r.field not in used) and h2 and r.rx.search(h2):
                hit = r
                break
        if hit is not None and hit.field != "amount":
            used.add(hit.field)
        out[idx] = hit
    return out


def _typed(raw: str, value_type: str, header: str, scale: Scale) -> tuple[Any, bool]:
    """(value, ok) for one cell. Text stays text; numbers are scaled where the type says so."""
    if value_type == "text":
        return raw.strip(), True
    n = read_number(raw)
    if n.value is None:
        if value_type == "year":
            m = re.search(r"(19|20)\d\d", raw)
            return (int(m.group(0)), True) if m else (None, True)
        return None, n.clean
    v = n.value
    cs = column_scale(header)
    sc = Scale(cs.money if cs and cs.money else scale.money, cs.sf if cs and cs.sf else scale.sf, scale.text)
    if value_type == "pct":
        if n.bps:
            v = v / 100
        return v, n.clean
    if value_type == "year":
        return int(v), n.clean
    if value_type in ("money", "sf"):
        out, _known = apply_scale(v, value_type, sc)
        return out, n.clean
    return v, n.clean


def jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        txt = format(v, "f")
        return txt.rstrip("0").rstrip(".") if "." in txt else txt
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    return v


def read_records(t: Table, kind: str, scale: Scale) -> list[tuple[int, str, dict[str, Any], float, list[float] | None]]:
    """(row_index, label, fields, mapped_share, bbox) for each row of a record table."""
    headers = [c.header for c in t.columns]
    out: list[tuple[int, str, dict[str, Any], float, list[float] | None]] = []

    if kind == "debt_maturity" and sum(1 for h in headers if is_yearish(h)) >= 3:
        # wide ladder: instruments down the side, years across the top
        for c in t.columns:
            if not is_yearish(c.header):
                continue
            ladder: dict[str, Any] = {}
            for r in t.rows:
                cell = r.cells.get(c.index)
                if cell is None or not r.label:
                    continue
                v, _ok = _typed(cell.text, "money", c.header, scale)
                if v is not None:
                    ladder[clean_label(r.label)] = v
            if ladder:
                out.append(
                    (
                        c.index,
                        clean_label(c.header),
                        {"amounts": ladder, "is_total": c.header.lower().startswith("total")},
                        1.0,
                        None,
                    )
                )
        return out

    mapping = map_columns(kind, headers)
    # two columns under one header usually means two header cells ran together on the page;
    # which value belongs to which field is then a guess, so neither column is mapped and
    # rows with values there are flagged ambiguous (and quarantined by their confidence)
    merged = {c.index for c in t.columns if c.header and headers.count(c.header) > 1}
    for i in merged:
        mapping[i] = None
    mapped_share = (sum(1 for v in mapping.values() if v is not None) / len(mapping)) if mapping else 0.0
    for r in t.rows:
        label = clean_label(r.label)
        if not label and not r.cells:
            continue
        fields: dict[str, Any] = {}
        extra: dict[str, Any] = {}
        amounts: dict[str, Any] = {}
        ok_all = True
        for c in t.columns:
            cell = r.cells.get(c.index)
            if cell is None:
                continue
            rule = mapping.get(c.index)
            if rule is None:
                extra[clean_label(c.header) or f"col{c.index}"] = cell.text
                continue
            v, ok = _typed(cell.text, rule.value_type, c.header, scale)
            ok_all = ok_all and ok
            if rule.field == "amount":
                if v is not None:
                    amounts[clean_label(c.header) or "Total"] = v
            elif v is not None:
                fields[rule.field] = v
        if amounts:
            fields["amounts"] = amounts
        if extra:
            fields["extra"] = extra
        if r.group:
            fields["group"] = clean_label(r.group)
        fields["is_total"] = r.is_total or bool(re.match(r"^(total|subtotal|top \d+)", label, re.I))
        if not ok_all:
            fields["unclean"] = True
        bbox = None
        cells = [r.label_cell, *r.cells.values()] if r.label_cell else list(r.cells.values())
        if cells:
            bbox = [
                round(min(x.x0 for x in cells), 1),
                round(min(x.top for x in cells), 1),
                round(max(x.x1 for x in cells), 1),
                round(max(x.bottom for x in cells), 1),
            ]
        ambiguous = set(r.ambiguous_cols) | {i for i in merged if i in r.cells}
        if ambiguous:
            fields["ambiguous_cols"] = sorted(ambiguous)
        out.append((r.index, label, fields, mapped_share, bbox))
    return out
