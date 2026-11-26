"""10-K Schedule III (Real Estate and Accumulated Depreciation) from XBRL.

Input: the filing's XBRL instance (EDGAR's extracted instance for inline filings) and,
when present, its label linkbase. Output: one ScheduleIIIRow per dimensioned row plus the
undimensioned totals, ready to become PropertyRecord objects.

Taxonomy-agnostic on purpose: filers use us-gaap names before 2019 and srt names after,
plus their own axes and members. Facts are picked by local name; any context with
dimensions is a row; the axis that names the property (DescriptionOfProperty or any axis
whose name mentions property, real estate or asset) gives the row its identity, the
geographic axis (stpr:XX members) gives the state, other axes give the type.
Only instants at the latest balance sheet date are used (roll-forward facts are durations).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from ..core import xmlsafe
from ..core.errors import ParseError
from ..properties import normalize as nz

PARSER_VERSION = "1.0"
XBRLI = "{http://www.xbrl.org/2003/instance}"
XBRLDI = "{http://xbrl.org/2006/xbrldi}"
LINK = "{http://www.xbrl.org/2003/linkbase}"
XLINK = "{http://www.w3.org/1999/xlink}"

# local-name pattern -> field (first match wins; order matters)
FIELD_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"Encumbrance", re.I), "encumbrances"),
    (re.compile(r"InitialCost.*Land(?!.*Building)", re.I), "initial_cost_land"),
    (re.compile(r"InitialCost.*(Building|Improvement)", re.I), "initial_cost_bldg"),
    (re.compile(r"(CostsCapitalized|CarryingCosts|SubsequentToAcquisition)", re.I), "costs_capitalized"),
    (re.compile(r"(CarryingAmountOfLand|CarriedAtCloseOfPeriodLand$|GrossAmount.*Land$)", re.I), "gross_land"),
    (
        re.compile(r"(CarryingAmountOfBuildings|CarriedAtCloseOfPeriod.*Building|GrossAmount.*Building)", re.I),
        "gross_bldg",
    ),
    (
        re.compile(r"(^RealEstateGrossAtCarryingValue$|CarriedAtCloseOfPeriodTotal|CarryingAmountTotal)", re.I),
        "gross_total",
    ),
    (
        re.compile(r"AccumulatedDepreciation$|AccumulatedDepreciationAmount|ScheduleIIIAccumulatedDepreciation", re.I),
        "accum_depreciation",
    ),
    (re.compile(r"DateOfConstruction|YearBuilt|YearConstructed", re.I), "construction_text"),
    (re.compile(r"DateAcquired|AcquisitionDate|YearAcquired", re.I), "acquired_text"),
    (re.compile(r"NumberOfUnits|NumberOfRealEstateProperties|AreaOfRealEstateProperty", re.I), "_size"),
]
CONCEPT_RX = re.compile(
    r"^(RealEstateAndAccumulatedDepreciation|RealEstateGrossAtCarryingValue$|RealEstateAccumulatedDepreciation$|"
    r"SECScheduleIII)",
    re.I,
)
ROLLFORWARD_RX = re.compile(
    r"(Additions|Deductions|AcquisitionsThroughForeclosure|Disposals|CostOfRealEstateSold|Writedown|Impairment|"
    r"PeriodIncrease|PeriodDecrease|OtherAdditions|OtherDeductions)",
    re.I,
)
PROPERTY_AXIS_RX = re.compile(
    r"(DescriptionOfProperty|Property|RealEstate|Asset|Investment|Building|Facilit|Communit|Hotel|Center)", re.I
)
GEO_AXIS_RX = re.compile(r"Geograph|StatesAxis|StateAxis|Market|Location|City|Region|Msa", re.I)
NUMERIC_FIELDS = (
    "encumbrances", "initial_cost_land", "initial_cost_bldg", "costs_capitalized",
    "gross_land", "gross_bldg", "gross_total", "accum_depreciation",
)  # fmt: skip


@dataclass
class ScheduleIIIRow:
    row_key: str  # stable within the filing: sorted dimension members
    label: str
    property_member: str | None
    is_total: bool = False
    is_aggregate: bool = False
    aggregate_count: int | None = None
    name: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    type_hint: str | None = None
    year_built: int | None = None
    year_renovated: int | None = None
    acquired_text: str | None = None
    acquired_date: date | None = None
    values: dict[str, Decimal] = field(default_factory=dict)
    dims: dict[str, str] = field(default_factory=dict)


@dataclass
class ScheduleIII:
    as_of: date | None
    rows: list[ScheduleIIIRow]
    totals: dict[str, Decimal]
    facts_used: int = 0

    @property
    def property_rows(self) -> list[ScheduleIIIRow]:
        return [r for r in self.rows if not r.is_total]

    def reconciliation(self) -> dict[str, Decimal | None]:
        """Sum of row gross_total vs the reported total (None when either is missing)."""
        rows = [v for r in self.property_rows if (v := r.values.get("gross_total")) is not None]
        s = sum(rows, Decimal(0)) if rows else None
        t = self.totals.get("gross_total")
        return {"rows_sum": s, "reported_total": t, "diff_pct": (abs(s - t) / t * 100) if s is not None and t else None}


def _local(tag: str) -> tuple[str, str]:
    if tag.startswith("{"):
        ns, _, name = tag[1:].partition("}")
        return ns, name
    return "", tag


def _member_name(qname: str) -> str:
    return qname.split(":", 1)[-1]


def split_camel(s: str) -> str:
    s = re.sub(r"Member$", "", s)
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[A-Za-z])(?=\d)", " ", s)
    return " ".join(s.split())


def parse_labels(xml: bytes | None) -> dict[str, str]:
    """Label linkbase -> {element local id (e.g. pld_FooMember): standard label}."""
    if not xml:
        return {}
    try:
        root = xmlsafe.fromstring(xml)
    except ParseError:
        return {}  # labels are a nicety: fall back to member names
    loc: dict[str, str] = {}
    lab: dict[str, tuple[str, str]] = {}
    arcs: list[tuple[str, str]] = []
    for el in root.iter():
        _, name = _local(el.tag)
        if name == "loc":
            href = el.get(f"{XLINK}href", "")
            loc[el.get(f"{XLINK}label", "")] = href.rsplit("#", 1)[-1]
        elif name == "label":
            role = el.get(f"{XLINK}role", "")
            lab[el.get(f"{XLINK}label", "") + "|" + role] = (role, (el.text or "").strip())
        elif name == "labelArc":
            arcs.append((el.get(f"{XLINK}from", ""), el.get(f"{XLINK}to", "")))
    by_label: dict[str, dict[str, str]] = defaultdict(dict)
    for k, (role, text) in lab.items():
        by_label[k.split("|", 1)[0]][role] = text
    out: dict[str, str] = {}
    for frm, to in arcs:
        el_id = loc.get(frm)
        roles = by_label.get(to, {})
        best: str | None = (
            roles.get("http://www.xbrl.org/2003/role/label")
            or roles.get("http://www.xbrl.org/2003/role/terseLabel")
            or next(iter(roles.values()), None)
        )
        if el_id and best and (el_id not in out):
            out[el_id] = re.sub(r"\s*\[Member\]$", "", best)
    return out


def _num(text: str | None, scale: str | None = None) -> Decimal | None:
    if text is None:
        return None
    t = text.strip().replace(",", "")
    try:
        v = Decimal(t)
    except InvalidOperation:
        return None
    return v


def _date(s: str | None) -> date | None:
    try:
        return date.fromisoformat((s or "").strip()[:10])
    except ValueError:
        return None


def field_for(local_name: str) -> str | None:
    if not CONCEPT_RX.search(local_name) or ROLLFORWARD_RX.search(local_name):
        return None
    for rx, f in FIELD_RULES:
        if rx.search(local_name):
            return f
    if re.search(r"Gross|Carrying", local_name, re.I):
        return "gross_total"
    return None


def split_label(label: str) -> tuple[str, nz.Location]:
    """'Plaza Center, Dallas, TX' -> ('Plaza Center', Location('Dallas','TX')).
    'Dallas, TX' alone -> (label, Location('Dallas','TX'))."""
    parts = [p.strip() for p in re.split(r",|\s[-|]\s", label) if p.strip()]
    for n in (2, 1):
        if len(parts) > n:
            loc = nz.parse_location(", ".join(parts[-n:]))
            if loc.state:
                if n == 1 and loc.city is None and len(parts) >= 3:
                    loc = nz.Location(parts[-2], loc.state, loc.country)
                    return ", ".join(parts[:-2]) or label, loc
                return ", ".join(parts[:-n]) or label, loc
    loc = nz.parse_location(label)
    return label, loc


def parse_schedule_iii(instance: bytes, labels: bytes | None = None) -> ScheduleIII:
    root = xmlsafe.fromstring(instance)
    lab = parse_labels(labels)
    contexts: dict[str, tuple[date | None, dict[str, str]]] = {}
    for ctx in root.iter(f"{XBRLI}context"):
        inst = ctx.find(f"{XBRLI}period/{XBRLI}instant")
        dims: dict[str, str] = {}
        for em in ctx.iter(f"{XBRLDI}explicitMember"):
            dims[em.get("dimension", "")] = (em.text or "").strip()
        for tm in ctx.iter(f"{XBRLDI}typedMember"):
            val = "".join(tm.itertext()).strip()
            dims[tm.get("dimension", "")] = "typed:" + val
        contexts[ctx.get("id", "")] = (_date(inst.text) if inst is not None else None, dims)

    facts: list[tuple[str, date, dict[str, str], str]] = []  # field, instant, dims, text
    for el in root:
        ns, name = _local(el.tag)
        if ns.startswith("http://www.xbrl.org") or ns == "http://xbrl.org/2006/xbrldi":
            continue
        f = field_for(name)
        if f is None:
            continue
        ref = el.get("contextRef")
        if ref not in contexts:
            continue
        when, dims = contexts[ref]
        if when is None or el.get("{http://www.w3.org/2001/XMLSchema-instance}nil") == "true":
            continue
        facts.append((f, when, dims, el.text or ""))
    if not facts:
        return ScheduleIII(None, [], {}, 0)
    as_of = max(i for _, i, _, _ in facts)
    facts = [x for x in facts if x[1] == as_of]

    groups: dict[tuple[tuple[str, str], ...], dict[str, str]] = defaultdict(dict)
    for f, _i, dims, text in facts:
        key = tuple(sorted(dims.items()))
        g = groups[key]
        if f == "costs_capitalized" and f in g:
            a, b = _num(g[f]), _num(text)
            if a is not None and b is not None:
                g[f] = str(a + b)
            continue
        g.setdefault(f, text)

    totals: dict[str, Decimal] = {}
    rows: list[ScheduleIIIRow] = []
    for key, vals in groups.items():
        dims = dict(key)
        numeric = {k: v for k in NUMERIC_FIELDS if (v := _num(vals.get(k))) is not None}
        if not dims:
            totals = numeric
            continue
        prop_axis = next(
            (a for a in dims if PROPERTY_AXIS_RX.search(_member_name(a)) and not GEO_AXIS_RX.search(_member_name(a))),
            None,
        )
        geo_axis = next((a for a in dims if GEO_AXIS_RX.search(_member_name(a)) and a != prop_axis), None)
        other = [a for a in dims if a not in (prop_axis, geo_axis)]

        def label_of(member: str) -> str:
            if member.startswith("typed:"):
                return member[6:]
            local = member.replace(":", "_")
            return lab.get(local) or split_camel(_member_name(member))

        r = ScheduleIIIRow(
            row_key="|".join(f"{_member_name(a)}={_member_name(v)}" for a, v in sorted(dims.items())),
            label="",
            property_member=dims.get(prop_axis) if prop_axis else None,
            dims={_member_name(a): v for a, v in dims.items()},
            values=numeric,
        )
        state = None
        city = None
        if geo_axis:
            gm = dims[geo_axis]
            if gm.startswith("stpr:") or re.match(r"^[a-z\-]+:[A-Z]{2}$", gm):
                state = nz.state_code(_member_name(gm))
            else:
                gl = nz.parse_location(label_of(gm))
                city, state = gl.city, gl.state or nz.state_code(label_of(gm))
        if prop_axis:
            r.label = label_of(dims[prop_axis])
            name, loc = split_label(r.label)
            r.name = name
            r.city = loc.city or city
            r.state = loc.state or (state if state in nz.STATE_CODES or state in nz.PROVINCES.values() else None)
            r.country = loc.country or ("US" if r.state in nz.STATE_CODES else None)
            agg, n = nz.aggregate_signal(name)
            r.is_aggregate, r.aggregate_count = agg, n
        else:
            r.label = " / ".join(label_of(dims[a]) for a in sorted(dims))
            r.name = r.label
            r.city, r.state = city, state if state in nz.STATE_CODES else None
            r.country = "US" if r.state else None
            r.is_aggregate = True
            r.aggregate_count = nz.aggregate_signal(r.label)[1]
        if other:
            r.type_hint = " ".join(label_of(dims[a]) for a in other)
        ct = vals.get("construction_text")
        if ct:
            r.year_built = nz.first_year(ct)
            ly = nz.last_year(ct)
            r.year_renovated = ly if ly and r.year_built and ly > r.year_built else None
        at = vals.get("acquired_text")
        if at:
            r.acquired_text = at.strip()[:64]
            r.acquired_date = _date(at)
        rows.append(r)

    # one row per property member: keep the dimension combination with the most facts
    best: dict[str, ScheduleIIIRow] = {}
    rest: list[ScheduleIIIRow] = []
    for r in rows:
        if r.property_member is None:
            rest.append(r)
            continue
        cur = best.get(r.property_member)
        if cur is None or (len(r.values), len(r.dims)) > (len(cur.values), len(cur.dims)):
            best[r.property_member] = r
    # when properties are tagged, rows with only geography/type are subtotals: drop them
    final = list(best.values()) if best else rest
    final.sort(key=lambda r: r.row_key)
    return ScheduleIII(as_of, final, totals, len(facts))
