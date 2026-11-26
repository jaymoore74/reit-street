"""Load a model's inputs file (TOML).

One file per company and period, e.g. ``models/inputs/ess_2026q3.toml``. It holds:

* ``[model]``        ticker, company, sector, period label and dates.
* ``[sources.<id>]`` the documents every number is cited to (title, url, date).
* ``[[inputs]]``     sourced numbers: key, label, value, unit, source, page, optional note and section.
* ``[[markets]]``    one row per market (county / MSA): units, NOI shares and the cap rate and growth estimates.
* ``[[debt]]``       one row per debt tranche: principal, coupon, years to maturity, fixed flag.
* ``[[assumptions]]`` analyst estimates: key, label, value, unit, rationale and section.

Market and debt rows are expanded into ordinary inputs and assumptions so every number goes through the same
citation checks.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

UNIT_FORMATS = {
    "$000": "num",
    "$": "ps",
    "$/unit": "dollar",
    "%": "pct",
    "x": "mult",
    "yrs": "dec1",
    "units": "num",
    "000 sh": "num",
    "flag": "int",
    "months": "int",
    "bp": "bp",
}


class InputsError(ValueError):
    pass


@dataclass(frozen=True)
class Source:
    id: str
    title: str
    url: str
    date: str


@dataclass(frozen=True)
class Item:
    key: str
    label: str
    value: float
    unit: str
    section: str
    source: str = ""
    page: str = ""
    note: str = ""
    rationale: str = ""

    @property
    def fmt(self) -> str:
        return UNIT_FORMATS.get(self.unit, "num")


@dataclass(frozen=True)
class Market:
    key: str
    name: str


@dataclass(frozen=True)
class Tranche:
    key: str
    name: str


@dataclass(frozen=True)
class FfoItem:
    key: str
    label: str
    kind: str  # "ffo" (net income to FFO) or "core" (FFO to Core FFO)


@dataclass
class ModelInputs:
    ticker: str
    company: str
    sector: str
    period: str
    as_of: str
    price_date: str
    sources: dict[str, Source] = field(default_factory=dict)
    inputs: list[Item] = field(default_factory=list)
    assumptions: list[Item] = field(default_factory=list)
    markets: list[Market] = field(default_factory=list)
    tranches: list[Tranche] = field(default_factory=list)
    ffo_items: list[FfoItem] = field(default_factory=list)
    forecast_years: list[int] = field(default_factory=list)
    quarter_label: str = ""
    ytd_label: str = ""
    path: str = ""

    def input_keys(self) -> set[str]:
        return {i.key for i in self.inputs}

    def assumption_keys(self) -> set[str]:
        return {a.key for a in self.assumptions}

    def value(self, key: str) -> float:
        for i in (*self.inputs, *self.assumptions):
            if i.key == key:
                return i.value
        raise KeyError(key)


def _req(d: dict[str, Any], k: str, where: str) -> Any:
    if k not in d:
        raise InputsError(f"{where}: missing {k!r}")
    return d[k]


def load_inputs(path: str | Path) -> ModelInputs:
    path = Path(path)
    with path.open("rb") as f:
        raw = tomllib.load(f)
    m = _req(raw, "model", str(path))
    mi = ModelInputs(
        ticker=str(_req(m, "ticker", "model")),
        company=str(_req(m, "company", "model")),
        sector=str(_req(m, "sector", "model")),
        period=str(_req(m, "period", "model")),
        as_of=str(_req(m, "as_of", "model")),
        price_date=str(_req(m, "price_date", "model")),
        forecast_years=[int(y) for y in _req(m, "forecast_years", "model")],
        quarter_label=str(_req(m, "quarter_label", "model")),
        ytd_label=str(_req(m, "ytd_label", "model")),
        path=str(path),
    )
    for sid, s in raw.get("sources", {}).items():
        mi.sources[sid] = Source(sid, str(_req(s, "title", sid)), str(s.get("url", "")), str(s.get("date", "")))

    for i, d in enumerate(raw.get("inputs", [])):
        where = f"inputs[{i}]"
        mi.inputs.append(
            Item(
                key=str(_req(d, "key", where)),
                label=str(_req(d, "label", where)),
                value=float(_req(d, "value", where)),
                unit=str(_req(d, "unit", where)),
                section=str(d.get("section", "")),
                source=str(_req(d, "source", where)),
                page=str(_req(d, "page", where)),
                note=str(d.get("note", "")),
            )
        )

    for i, d in enumerate(raw.get("markets", [])):
        where = f"markets[{i}]"
        k = str(_req(d, "key", where))
        name = str(_req(d, "name", where))
        mi.markets.append(Market(k, name))
        src, page = str(_req(d, "source", where)), str(_req(d, "page", where))
        sec = "Portfolio by market"
        for field_, label, unit in (
            ("units", "consolidated units", "units"),
            ("noi_pct", "% of consolidated NOI", "%"),
            ("jv_units", "co-investment units (100%)", "units"),
            ("jv_noi_pct", "% of co-investment NOI at share", "%"),
        ):
            mi.inputs.append(
                Item(f"mk_{field_}_{k}", f"{name}: {label}", float(_req(d, field_, where)), unit, sec, src, page)
            )
        rat = str(_req(d, "rationale", where))
        mi.assumptions.append(
            Item(f"as_cap_{k}", f"{name}: cap rate", float(_req(d, "cap_rate", where)), "%", "Cap rates", rationale=rat)
        )
        mi.assumptions.append(
            Item(
                f"as_g_{k}",
                f"{name}: forward NOI growth",
                float(_req(d, "growth", where)),
                "%",
                "Forward NOI growth",
                rationale=str(d.get("growth_rationale", rat)),
            )
        )

    for i, d in enumerate(raw.get("debt", [])):
        where = f"debt[{i}]"
        k = str(_req(d, "key", where))
        name = str(_req(d, "name", where))
        mi.tranches.append(Tranche(k, name))
        src, page = str(_req(d, "source", where)), str(_req(d, "page", where))
        note = str(d.get("note", ""))
        sec = "Debt"
        for field_, label, unit in (
            ("principal", "principal", "$000"),
            ("coupon", "weighted average rate", "%"),
            ("years", "years to maturity", "yrs"),
            ("fixed", "fixed rate (1) or floating / swapped (0)", "flag"),
            ("secured", "secured (1) or unsecured (0)", "flag"),
        ):
            mi.inputs.append(
                Item(f"db_{field_}_{k}", f"{name}: {label}", float(_req(d, field_, where)), unit, sec, src, page, note)
            )

    for i, d in enumerate(raw.get("ffo_items", [])):
        where = f"ffo_items[{i}]"
        k = str(_req(d, "key", where))
        label = str(_req(d, "label", where))
        kind = str(_req(d, "kind", where))
        if kind not in ("ffo", "core"):
            raise InputsError(f"{where}: kind must be 'ffo' or 'core'")
        mi.ffo_items.append(FfoItem(k, label, kind))
        src, page = str(_req(d, "source", where)), str(_req(d, "page", where))
        sec = "FFO reconciliation" if kind == "ffo" else "Core FFO adjustments"
        for suffix, col, plabel in (("q", "q", mi.quarter_label), ("ytd", "ytd", mi.ytd_label)):
            mi.inputs.append(
                Item(f"fr_{k}_{suffix}", f"{label} ({plabel})", float(_req(d, col, where)), "$000", sec, src, page)
            )

    for i, d in enumerate(raw.get("assumptions", [])):
        where = f"assumptions[{i}]"
        mi.assumptions.append(
            Item(
                key=str(_req(d, "key", where)),
                label=str(_req(d, "label", where)),
                value=float(_req(d, "value", where)),
                unit=str(_req(d, "unit", where)),
                section=str(d.get("section", "")),
                rationale=str(_req(d, "rationale", where)),
            )
        )

    keys = [x.key for x in (*mi.inputs, *mi.assumptions)]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes:
        raise InputsError(f"duplicate keys: {', '.join(dupes)}")
    for it in mi.inputs:
        if it.source not in mi.sources:
            raise InputsError(f"input {it.key} cites unknown source {it.source!r}")
    return mi
