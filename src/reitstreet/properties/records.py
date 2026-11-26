"""PropertyRecord: one property row from one source, the input to the property master.

Every loader (Schedule III, supplemental parser, websites, datasets, manual lists)
produces PropertyRecord objects and hands them to `stage_records`, which writes
stg_property_record. Nothing else writes that table.

For the supplemental parser:

    from reitstreet.properties.records import PropertyRecord, stage_records
    recs = [PropertyRecord(company_id=cid, source_kind="supplemental", as_of_date=period_end,
                           known_from=published, loader="supp_pdf", loader_version="1.0",
                           source_doc_id=doc_id, source_parse_id=parse_id, source_table_id=table_id,
                           source_page_no=page, source_row_index=row, source_sha256=sha,
                           name=..., city=..., state=..., rentable_sf=..., attributes={...})]
    ids = stage_records(db, recs, run_id)   # {record_key: record_id}
    # key your property-level metrics to record_id; the property id comes via wh_property_xref
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field, fields
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from . import normalize as nz

if TYPE_CHECKING:
    from ..core.db import Db

SOURCE_KINDS = ("sec_schedule_iii", "supplemental", "corporate_website", "public_dataset", "manual")
UNIT_KINDS = ("sf", "units", "beds", "keys", "sites", "acres", "towers", "screens", "mw", "other")
INTEREST_TYPES = ("fee", "ground_lease", "leasehold", "joint_venture", "mortgage", "unknown")
# Canonical attributes come from the highest-priority source (lower = better).
SOURCE_PRIORITY = {"manual": 0, "supplemental": 1, "corporate_website": 2, "sec_schedule_iii": 3, "public_dataset": 4}

Num = Decimal | float | int | None


@dataclass
class PropertyRecord:
    company_id: int
    source_kind: str
    as_of_date: date
    known_from: date
    loader: str
    loader_version: str
    source_row_id: int | None = None
    source_doc_id: int | None = None
    source_parse_id: int | None = None
    source_table_id: int | None = None
    source_page_no: int | None = None
    source_row_index: int | None = None
    source_sha256: str | None = None
    source_url: str | None = None
    accession_no: str | None = None
    source_property_ref: str | None = None
    name: str | None = None
    address_line: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None
    market: str | None = None
    raw_property_type: str | None = None
    type_code: str | None = None
    rentable_sf: Num = None
    unit_count: Num = None
    unit_kind: str | None = None
    land_acres: Num = None
    year_built: int | None = None
    year_renovated: int | None = None
    acquired_date: date | None = None
    acquired_text: str | None = None
    disposed_date: date | None = None
    ownership_pct: Num = None
    interest_type: str | None = None
    occupancy_pct: Num = None
    encumbrances: Num = None
    initial_cost_land: Num = None
    initial_cost_bldg: Num = None
    costs_capitalized: Num = None
    gross_land: Num = None
    gross_bldg: Num = None
    gross_total: Num = None
    accum_depreciation: Num = None
    is_aggregate: bool = False
    aggregate_count: int | None = None
    latitude: Num = None
    longitude: Num = None
    attributes: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ derived
    def validate(self) -> None:
        if self.source_kind not in SOURCE_KINDS:
            raise ValueError(f"unknown source_kind {self.source_kind!r}")
        if self.unit_kind is not None and self.unit_kind not in UNIT_KINDS:
            raise ValueError(f"unknown unit_kind {self.unit_kind!r}")
        if self.interest_type is not None and self.interest_type not in INTEREST_TYPES:
            raise ValueError(f"unknown interest_type {self.interest_type!r}")
        if not (self.name or self.address_line or self.source_property_ref):
            raise ValueError("a property record needs a name, an address or a source reference")
        if self.ownership_pct is not None and not (0 <= float(self.ownership_pct) <= 100):
            raise ValueError(f"ownership_pct out of range: {self.ownership_pct}")

    def tidy(self) -> PropertyRecord:
        """Fill state code and country from what was given; flag aggregate rows. Idempotent."""
        if self.state:
            self.state = nz.state_code(self.state)
        if not self.city and not self.state and self.market:
            loc = nz.parse_location(self.market)
            self.city, self.state = loc.city, loc.state
            self.country = self.country or loc.country
        if not self.country and self.state in nz.STATE_CODES:
            self.country = "US"
        if not self.is_aggregate and not self.address_line:
            agg, n = nz.aggregate_signal(self.name)
            if agg:
                self.is_aggregate, self.aggregate_count = True, self.aggregate_count or n
        return self

    @property
    def norm_name(self) -> str | None:
        return nz.normalize_name(self.name)

    @property
    def norm_address(self) -> str | None:
        return nz.normalize_street(self.address_line)

    @property
    def address_key(self) -> str | None:
        return nz.address_key(self.address_line, self.city, self.state, self.postal_code)

    def locator(self) -> str:
        """What identifies the source snapshot the row came from."""
        for v in (self.source_sha256, self.source_url, self.source_doc_id, self.accession_no):
            if v:
                return str(v)
        return ""

    def identity(self) -> str:
        if self.source_property_ref:
            return f"ref:{self.source_property_ref}"
        page, row = self.source_page_no or "", self.source_row_index or ""
        return f"row:{page}:{row}:{self.norm_name or ''}:{self.norm_address or ''}"

    def record_key(self) -> str:
        """Same source snapshot + same row + same content = same key, so reloads are idempotent.
        A parser fix that changes content gives a new key; the old row is retired by stage_records."""
        content = json.dumps(self.content(), sort_keys=True, default=str)
        parts = [
            str(self.company_id),
            self.source_kind,
            self.locator(),
            self.as_of_date.isoformat(),
            self.identity(),
            content,
        ]
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    def content(self) -> dict[str, Any]:
        skip = {"run_id", "loader_version"}
        return {k: v for k, v in asdict(self).items() if k not in skip and v not in (None, {}, False)}

    def row(self, run_id: int | None) -> dict[str, Any]:
        d = {f.name: getattr(self, f.name) for f in fields(self)}
        d["attributes"] = json.dumps(self.attributes, sort_keys=True, default=str) if self.attributes else None
        d["is_aggregate"] = int(self.is_aggregate)
        d.update(
            record_key=self.record_key(),
            norm_name=(self.norm_name or None) and self.norm_name[:255],
            norm_address=(self.norm_address or None) and self.norm_address[:255],
            address_key=self.address_key,
            run_id=run_id,
        )
        for k in ("name", "address_line", "source_property_ref"):
            if d[k]:
                d[k] = str(d[k])[:255]
        for k in ("city", "market", "raw_property_type"):
            if d[k]:
                d[k] = str(d[k])[:128]
        if d["acquired_text"]:
            d["acquired_text"] = str(d["acquired_text"])[:64]
        return d


COLUMNS = [f.name for f in fields(PropertyRecord)] + [
    "record_key",
    "norm_name",
    "norm_address",
    "address_key",
    "run_id",
]


class TypeClassifier:
    """Applies ref_property_type_map rules: reported type text first, then the name, then the
    company's sector. First match by priority wins."""

    def __init__(self, rules: Sequence[dict[str, Any]]):
        self.rules = sorted(
            [(int(r["priority"]), r["applies_to"], re.compile(r["pattern"], re.I), r["type_code"]) for r in rules],
            key=lambda x: x[0],
        )

    @classmethod
    def from_db(cls, db: Db) -> TypeClassifier:
        return cls(db.query("SELECT pattern, type_code, priority, applies_to FROM ref_property_type_map"))

    def classify(self, raw_type: str | None, name: str | None, company_sector: str | None) -> str | None:
        for applies_to, text in (("type", raw_type), ("name", name), ("company_sector", company_sector)):
            if not text:
                continue
            for _p, a, rx, code in self.rules:
                # type rules also apply to names: a name like "Sunrise Assisted Living" is informative
                if (a == applies_to or (a == "type" and applies_to == "name")) and rx.search(text):
                    return code
        return None


def stage_records(
    db: Db, records: Iterable[PropertyRecord], run_id: int | None, *, replace_scope: bool = True
) -> dict[str, int]:
    """Insert records (idempotent on record_key). Returns {record_key: record_id}.

    With replace_scope, live rows from the same company, source kind and source snapshot
    (locator + as-of date) that are not in this batch are retired: a reload of the same
    document with a better parser replaces the old rows instead of adding to them."""
    recs = []
    for r in records:
        r.tidy()
        r.validate()
        recs.append(r)
    if not recs:
        return {}
    cols = COLUMNS
    sql = f"INSERT IGNORE INTO stg_property_record ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})"
    db.executemany(sql, [r.row(run_id) for r in recs])
    keys = [r.record_key() for r in recs]
    ids: dict[str, int] = {}
    for i in range(0, len(keys), 1000):
        chunk = keys[i : i + 1000]
        ph = ", ".join(f":k{j}" for j in range(len(chunk)))
        # the same content seen again after a retirement (parser A -> B -> A) is live again
        db.execute(
            f"UPDATE stg_property_record SET retired_at = NULL WHERE record_key IN ({ph}) AND retired_at IS NOT NULL",
            {f"k{j}": k for j, k in enumerate(chunk)},
        )
        for row in db.query(
            f"SELECT record_id, record_key FROM stg_property_record WHERE record_key IN ({ph})",
            {f"k{j}": k for j, k in enumerate(chunk)},
        ):
            ids[row["record_key"]] = int(row["record_id"])
    if replace_scope:
        scopes = {(r.company_id, r.source_kind, r.locator(), r.as_of_date) for r in recs if r.locator()}
        for cid, kind, loc, asof in scopes:
            keep = [
                ids[r.record_key()]
                for r in recs
                if (r.company_id, r.source_kind, r.locator(), r.as_of_date) == (cid, kind, loc, asof)
            ]
            ph = ", ".join(f":i{j}" for j in range(len(keep)))
            db.execute(
                f"""UPDATE stg_property_record SET retired_at=CURRENT_TIMESTAMP(6)
                    WHERE company_id=:c AND source_kind=:k AND as_of_date=:a AND retired_at IS NULL
                      AND COALESCE(source_sha256, source_url, CAST(source_doc_id AS CHAR), accession_no, '')=:loc
                      AND record_id NOT IN ({ph})""",
                {"c": cid, "k": kind, "a": asof, "loc": loc, **{f"i{j}": v for j, v in enumerate(keep)}},
            )
    return ids
