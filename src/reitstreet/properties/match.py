"""Entity matching: which wh_property does each stg_property_record describe?

Pure code (no database) so every rule is unit tested. build.py feeds it the live
property index and the records to place, and writes the decisions.

Rules, strongest first (ADR 0010):
  1. source_ref   the same company's same source kind used the same property reference
  2. address      same normalized street address + ZIP5 (or street + city + state)
  3. name_city    name similarity within the same owner and city
  4. geo          within 75 m and name similarity >= 0.6
  5. new          nothing matched: a new property
A close call (name similarity 0.75 to 0.9, or several address hits) becomes a new property
with review_status needs_review and the candidates kept, so a person can merge it.
Two records from the same source snapshot never land on one property automatically.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from .normalize import name_tokens

MATCHER_VERSION = "1.0"
NAME_AUTO = 0.90
NAME_REVIEW = 0.75
GEO_METERS = 75.0
GEO_NAME = 0.60


def name_similarity(a: str | None, b: str | None) -> float:
    """0..1. Max of token-set overlap and character ratio on sorted tokens, so word order,
    suffixes and small typos all score high ("Plaza at Main" ~ "Main Plaza")."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = set(name_tokens(a)), set(name_tokens(b))
    if not ta or not tb:
        return 0.0
    na, nb = {t for t in ta if t.isdigit()}, {t for t in tb if t.isdigit()}
    if na and nb and na != nb:
        return 0.0  # "1000 Main" and "2000 Main" are different buildings
    inter = len(ta & tb)
    overlap = inter / min(len(ta), len(tb)) if inter else 0.0
    # a one-word name contained in a longer one ("Galleria" in "Galleria Dallas") is not enough alone
    if min(len(ta), len(tb)) == 1 and max(len(ta), len(tb)) > 1:
        overlap *= 0.85
    jacc = inter / len(ta | tb)
    ratio = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return round(max(min(overlap, (overlap + jacc) / 2 + 0.25), ratio), 4)


def meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


@dataclass
class Rec:
    record_id: int
    company_id: int
    source_kind: str
    snapshot: str  # source locator + as-of date: rows of one snapshot never merge
    source_ref: str | None = None
    norm_name: str | None = None
    address_key: str | None = None
    city: str | None = None
    state: str | None = None
    latitude: float | None = None
    longitude: float | None = None


@dataclass
class Prop:
    property_id: int
    companies: set[int] = field(default_factory=set)
    names: set[str] = field(default_factory=set)
    city_key: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    snapshots: set[str] = field(default_factory=set)


@dataclass
class Decision:
    record_id: int
    property_id: int | None  # None: create a new property (build.py assigns the id)
    method: str
    score: float | None
    review_status: str = "auto"
    candidates: list[dict[str, Any]] = field(default_factory=list)
    new_key: int | None = None  # groups records that share one new property within a batch


def city_key(city: str | None, state: str | None) -> str | None:
    if not city or not state:
        return None
    return f"{' '.join(city.upper().split())}|{state.upper()}"


class Index:
    """In-memory view of live properties and their aliases."""

    def __init__(self) -> None:
        self.props: dict[int, Prop] = {}
        self.by_ref: dict[tuple[int, str, str], int] = {}
        self.by_addr: dict[str, set[int]] = defaultdict(set)
        self.by_company_city: dict[tuple[int, str], set[int]] = defaultdict(set)
        self.cells: dict[tuple[int, int], set[int]] = defaultdict(set)
        self._next_temp = -1

    # ------------------------------------------------------------------ build
    def add_property(self, p: Prop) -> None:
        self.props[p.property_id] = p
        if p.city_key:
            for c in p.companies:
                self.by_company_city[(c, p.city_key)].add(p.property_id)
        if p.latitude is not None and p.longitude is not None:
            self.cells[_cell(p.latitude, p.longitude)].add(p.property_id)

    def add_alias(
        self, property_id: int, kind: str, value: str, company_id: int | None, source_kind: str | None
    ) -> None:
        if kind == "source_ref" and company_id is not None and source_kind:
            self.by_ref[(company_id, source_kind, value)] = property_id
        elif kind == "address":
            self.by_addr[value].add(property_id)
        elif kind == "name":
            self.props[property_id].names.add(value)

    def absorb(self, pid: int, r: Rec) -> None:
        """Teach the index what a record just placed on pid tells us, so later records match."""
        p = self.props.get(pid)
        if p is None:
            p = Prop(pid)
            self.props[pid] = p
        p.companies.add(r.company_id)
        p.snapshots.add(r.snapshot)
        if r.norm_name:
            p.names.add(r.norm_name)
        ck = city_key(r.city, r.state)
        if ck:
            p.city_key = p.city_key or ck
            self.by_company_city[(r.company_id, ck)].add(pid)
        if r.address_key:
            self.by_addr[r.address_key].add(pid)
        if r.source_ref:
            self.by_ref[(r.company_id, r.source_kind, r.source_ref)] = pid
        if r.latitude is not None and r.longitude is not None and p.latitude is None:
            p.latitude, p.longitude = r.latitude, r.longitude
            self.cells[_cell(r.latitude, r.longitude)].add(pid)

    def temp_id(self) -> int:
        self._next_temp -= 1
        return self._next_temp

    # ------------------------------------------------------------------ query
    def best_name(self, pid: int, norm_name: str | None) -> float:
        p = self.props.get(pid)
        if p is None or not norm_name:
            return 0.0
        return max((name_similarity(norm_name, n) for n in p.names), default=0.0)

    def near(self, lat: float, lon: float) -> Iterable[int]:
        cx, cy = _cell(lat, lon)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                yield from self.cells.get((cx + dx, cy + dy), ())


def _cell(lat: float, lon: float) -> tuple[int, int]:
    return (math.floor(lat * 500), math.floor(lon * 500))  # ~200 m cells


def place(index: Index, records: Iterable[Rec]) -> list[Decision]:
    """Decide a property for each record, in order. New properties get negative temp ids
    in the index (Decision.new_key) so later records in the same batch can match them."""
    out: list[Decision] = []
    for r in records:
        d = _decide(index, r)
        if d.property_id is None:
            d.new_key = index.temp_id()
            index.absorb(d.new_key, r)
        else:
            index.absorb(d.property_id, r)
        out.append(d)
    return out


def _same_snapshot(index: Index, pid: int, r: Rec) -> bool:
    p = index.props.get(pid)
    return bool(p and r.snapshot in p.snapshots)


def _decide(index: Index, r: Rec) -> Decision:
    # 1. the source's own reference
    if r.source_ref:
        pid = index.by_ref.get((r.company_id, r.source_kind, r.source_ref))
        if pid is not None and not _same_snapshot(index, pid, r):
            return Decision(r.record_id, _real(pid), "source_ref", 1.0)
    # 2. address
    if r.address_key:
        hits = [p for p in index.by_addr.get(r.address_key, ()) if not _same_snapshot(index, p, r)]
        if len(hits) == 1:
            return Decision(r.record_id, _real(hits[0]), "address", 1.0)
        if len(hits) > 1:
            ranked = sorted(hits, key=lambda p: -index.best_name(p, r.norm_name))
            best = ranked[0]
            if index.best_name(best, r.norm_name) >= NAME_AUTO:
                return Decision(r.record_id, _real(best), "address", 1.0)
            return _review(r, "address", [(p, index.best_name(p, r.norm_name)) for p in ranked[:5]])
    # 3. name within the same owner and city
    ck = city_key(r.city, r.state)
    if ck and r.norm_name:
        cands = [
            (p, index.best_name(p, r.norm_name))
            for p in index.by_company_city.get((r.company_id, ck), ())
            if not _same_snapshot(index, p, r)
        ]
        cands = sorted((c for c in cands if c[1] >= NAME_REVIEW), key=lambda c: -c[1])
        if cands:
            top = cands[0]
            runner = cands[1][1] if len(cands) > 1 else 0.0
            if top[1] >= NAME_AUTO and top[1] - runner >= 0.05:
                return Decision(r.record_id, _real(top[0]), "name_city", top[1])
            return _review(r, "name_city", cands[:5])
    # 4. geocode distance
    if r.latitude is not None and r.longitude is not None and r.norm_name:
        geo = []
        for p in set(index.near(r.latitude, r.longitude)):
            prop = index.props[p]
            if prop.latitude is None or prop.longitude is None or _same_snapshot(index, p, r):
                continue
            dist = meters(r.latitude, r.longitude, prop.latitude, prop.longitude)
            sim = index.best_name(p, r.norm_name)
            if dist <= GEO_METERS and sim >= GEO_NAME:
                geo.append((p, sim, dist))
        if len(geo) == 1:
            return Decision(r.record_id, _real(geo[0][0]), "geo", geo[0][1])
        if len(geo) > 1:
            return _review(r, "geo", [(p, s) for p, s, _ in sorted(geo, key=lambda g: g[2])[:5]])
    return Decision(r.record_id, None, "new", None)


def _real(pid: int) -> int | None:
    """Negative ids are properties created earlier in this batch; build.py resolves them."""
    return pid


def _review(r: Rec, method: str, cands: list[tuple[int, float]]) -> Decision:
    return Decision(
        r.record_id,
        None,
        "new",
        cands[0][1] if cands else None,
        "needs_review",
        [{"property_id": p, "score": round(s, 4), "rule": method} for p, s in cands],
    )
