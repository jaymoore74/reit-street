"""Parse data.sec.gov/api/xbrl/companyfacts JSON.

What this API holds: standard taxonomies only (us-gaap, dei, srt, ...). Company
extension tags (a REIT's own FFO tag) and dimensions (segments) are not in it.
FFO, AFFO, NOI and same-store data therefore come from supplementals.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from ..core.errors import ParseError

PARSER_VERSION = "cf-1.0"
MAX_ABS = Decimal("1e22")  # DECIMAL(28,6) limit


@dataclass(frozen=True)
class Concept:
    taxonomy: str
    tag: str
    label: str | None
    description: str | None


@dataclass(frozen=True)
class Fact:
    taxonomy: str
    tag: str
    unit: str
    period_start: date | None
    period_end: date
    value: Decimal
    accession_no: str
    fy: int | None
    fp: str | None
    form: str | None
    filed: date
    frame: str | None


def _d(s: Any) -> date | None:
    return date.fromisoformat(s) if s else None


def parse_companyfacts(body: bytes) -> tuple[list[Concept], Iterator[Fact]]:
    try:
        data = json.loads(body)
        facts = data["facts"]
    except (ValueError, KeyError, TypeError) as e:
        raise ParseError(f"not a companyfacts document: {e}") from e
    concepts = [
        Concept(tax, tag[:255], (b.get("label") or "")[:512] or None, b.get("description"))
        for tax, cs in facts.items()
        for tag, b in cs.items()
    ]

    def it() -> Iterator[Fact]:
        for tax, cs in facts.items():
            for tag, body_ in cs.items():
                for unit, rows in (body_.get("units") or {}).items():
                    for f in rows:
                        try:
                            val = Decimal(str(f["val"]))
                            end, filed = _d(f["end"]), _d(f["filed"])
                        except (InvalidOperation, KeyError, TypeError, ValueError):
                            continue
                        if end is None or filed is None or not val.is_finite() or abs(val) >= MAX_ABS:
                            continue
                        yield Fact(
                            tax,
                            tag[:255],
                            unit[:32],
                            _d(f.get("start")),
                            end,
                            val,
                            f["accn"],
                            f.get("fy"),
                            f.get("fp") or None,
                            f.get("form"),
                            filed,
                            f.get("frame"),
                        )

    return concepts, it()
