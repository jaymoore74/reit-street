"""Ownership spells from sightings. Pure code.

A sighting is a live record of a property published by an owner (company) as of a date.
A company's snapshot is one (source kind, as-of date) list of its properties. For each
(property, company) the timeline is every snapshot of the source kinds that ever listed the
property for that company. Consecutive snapshots that list it form a spell. A spell ends
when a later snapshot of the same kind leaves the property out, but only if that snapshot
is about as large as the one that last listed it (>= 80% of its rows), so a partial list
(a supplemental that only shows stabilized assets this quarter) never sells a building.
Reported acquisition and disposition dates win over inferred ones.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

COMPLETE_SHARE = 0.8


@dataclass(frozen=True)
class Sighting:
    as_of: date
    source_kind: str
    record_id: int
    known_from: date
    ownership_pct: Decimal | float | None = None
    interest_type: str | None = None
    acquired_date: date | None = None
    disposed_date: date | None = None


@dataclass
class Spell:
    spell_no: int
    acquired_date: date | None
    acquired_basis: str
    disposed_date: date | None
    disposed_basis: str | None
    first_seen_as_of: date
    last_seen_as_of: date
    known_from: date
    last_record_id: int
    ownership_pct: Decimal | float | None
    interest_type: str | None

    def row_hash(self) -> str:
        d = {k: v for k, v in self.__dict__.items() if k not in ("last_record_id", "known_from")}
        return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()

    def as_row(self) -> dict[str, Any]:
        return {**self.__dict__, "row_hash": self.row_hash()}


def compute_spells(sightings: Iterable[Sighting], snapshot_sizes: dict[tuple[str, date], int]) -> list[Spell]:
    """snapshot_sizes: {(source_kind, as_of): live record count} for this company."""
    seen = sorted(sightings, key=lambda s: (s.as_of, s.source_kind, s.record_id))
    if not seen:
        return []
    kinds = {s.source_kind for s in seen}
    timeline = sorted({d for (k, d) in snapshot_sizes if k in kinds} | {s.as_of for s in seen})
    by_date: dict[date, list[Sighting]] = {}
    for s in seen:
        by_date.setdefault(s.as_of, []).append(s)

    spells: list[Spell] = []
    cur: list[Sighting] = []
    for d in timeline:
        here = by_date.get(d)
        if here:
            cur.extend(here)
            continue
        if not cur:
            continue
        last = cur[-1]
        size_then = snapshot_sizes.get((last.source_kind, last.as_of), 0)
        size_now = snapshot_sizes.get((last.source_kind, d))
        if size_now is not None and size_now >= COMPLETE_SHARE * size_then:
            spells.append(_close(cur, len(spells) + 1, d))
            cur = []
    if cur:
        spells.append(_close(cur, len(spells) + 1, None))
    return spells


def _close(cur: list[Sighting], no: int, missing_on: date | None) -> Spell:
    reported_acq = [s.acquired_date for s in cur if s.acquired_date]
    reported_disp = [s.disposed_date for s in cur if s.disposed_date]
    first, last = cur[0], cur[-1]
    latest = max(cur, key=lambda s: (s.as_of, s.known_from, s.record_id))
    pct = next(
        (s.ownership_pct for s in sorted(cur, key=lambda s: s.as_of, reverse=True) if s.ownership_pct is not None), None
    )
    itype = next((s.interest_type for s in sorted(cur, key=lambda s: s.as_of, reverse=True) if s.interest_type), None)
    acq = min(reported_acq) if reported_acq else first.as_of
    if reported_disp:
        disp, basis = max(reported_disp), "reported"
    elif missing_on is not None:
        disp, basis = missing_on, "not_listed"
    else:
        disp, basis = None, None
    if disp is not None and acq is not None and disp < acq:
        acq = disp
    return Spell(
        spell_no=no,
        acquired_date=acq,
        acquired_basis="reported" if reported_acq else "first_seen",
        disposed_date=disp,
        disposed_basis=basis,
        first_seen_as_of=first.as_of,
        last_seen_as_of=last.as_of,
        known_from=min(s.known_from for s in cur),
        last_record_id=latest.record_id,
        ownership_pct=pct,
        interest_type=itype,
    )
