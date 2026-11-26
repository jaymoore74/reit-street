"""Offline evaluation of the supplemental parser: `reitstreet parse-eval FOLDER`.

Runs the parser over a folder of PDFs with no database and no network, applies the
seed mapping rules (reference/data/metric_map.csv) in Python, and reports per file and in
total: tables found and classified, values read, records read, the quarantine rate, and
which of the metrics the sector catalog calls core were found. This is how parser
changes are measured before they ship, and how a batch of older supplementals is
checked before it is imported.

Ticker and period come from the file name (PLD_2024Q3_supplemental.pdf) the same way
`import-archive` reads them; a universe CSV (--universe) supplies each ticker's sector.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from ...core.errors import ParseError
from ...sources.supplementals.archive import period_from_name, ticker_from_name
from ..documents import DocMeta, ParsedItem
from .parser import SupplementalPdfParser


def _ref(name: str) -> list[dict[str, str]]:
    text = resources.files("reitstreet.reference").joinpath("data", name).read_text(encoding="utf-8")
    return list(csv.DictReader(text.splitlines()))


@dataclass(frozen=True)
class Rule:
    metric_code: str
    label: re.Pattern[str]
    exclude: re.Pattern[str] | None
    col_role: str
    table_kind: re.Pattern[str] | None
    priority: int


def load_rules() -> list[Rule]:
    out = []
    for r in _ref("metric_map.csv"):
        if r["source_type"] != "supplemental" or not r["label_regex"]:
            continue
        out.append(
            Rule(
                r["metric_code"],
                re.compile(r["label_regex"], re.I),
                re.compile(r["exclude_regex"], re.I) if r["exclude_regex"] else None,
                r.get("col_role") or "value",
                re.compile(r["table_kind_regex"], re.I) if r.get("table_kind_regex") else None,
                int(r["priority"] or 100),
            )
        )
    return out


def core_metrics_by_sector() -> dict[str, set[str]]:
    """Universe sector label (lower case) -> metric codes the catalog calls core for it."""
    parents = {r["sector_code"]: r["parent_code"] for r in _ref("sectors.csv")}
    core: dict[str, set[str]] = {}
    for r in _ref("metric_sectors.csv"):
        if r["relevance"] == "core":
            core.setdefault(r["sector_code"], set()).add(r["metric_code"])
    grain = {r["metric_code"]: r.get("grain") or "company" for r in _ref("metrics.csv")}
    out: dict[str, set[str]] = {}
    for r in _ref("sector_labels.csv"):
        codes = [c for c in r["sector_codes"].split(";") if c]
        want: set[str] = set()
        for c in codes:
            for s in (c, parents.get(c, ""), "all"):
                want |= core.get(s, set())
        # financial-statement lines usually come from XBRL; the supplemental is judged on the rest
        out[r["universe_label"].strip().lower()] = {m for m in want if grain.get(m) == "company"}
    return out


XBRL_FIRST = {
    "TOTAL_REVENUE",
    "NET_INCOME",
    "NET_INCOME_COMMON",
    "TOTAL_ASSETS",
    "TOTAL_LIABILITIES",
    "TOTAL_EQUITY",
    "CASH",
    "TOTAL_DEBT",
    "SHARES_OUTSTANDING",
    "DILUTED_SHARES_WAVG",
    "EPS_DILUTED",
    "DIVIDENDS_PER_SHARE",
    "INTEREST_EXPENSE",
    "RE_DEPRECIATION",
    "REAL_ESTATE_GROSS",
    "ACCUM_DEPRECIATION",
    "REAL_ESTATE_NET",
}


def match_metrics(items: list[ParsedItem], kinds: dict[tuple[int, int], str | None], rules: list[Rule]) -> set[str]:
    found: set[str] = set()
    for i in items:
        if i.quarantined or i.value_numeric is None:
            continue
        label = i.metric_label or i.row_label
        kind = kinds.get((i.page_no, i.table_index or 0)) or ""
        for r in rules:
            if r.metric_code in found:
                continue
            if r.col_role != "any" and r.col_role != i.col_role:
                continue
            if r.table_kind and not r.table_kind.search(kind):
                continue
            if r.label.search(label) and not (r.exclude and r.exclude.search(label)):
                found.add(r.metric_code)
    return found


@dataclass
class FileResult:
    file: str
    ticker: str | None = None
    sector: str | None = None
    period_end: str | None = None
    status: str = "ok"
    error: str | None = None
    pages: int = 0
    tables: int = 0
    tables_classified: int = 0
    items: int = 0
    items_quarantined: int = 0
    records: int = 0
    records_quarantined: int = 0
    kinds: dict[str, int] = field(default_factory=dict)
    record_kinds: dict[str, int] = field(default_factory=dict)
    metrics_found: list[str] = field(default_factory=list)
    core_expected: int = 0
    core_found: int = 0
    core_missing: list[str] = field(default_factory=list)


def evaluate_file(
    path: Path,
    parser: SupplementalPdfParser,
    rules: list[Rule],
    sectors: dict[str, str],
    core: dict[str, set[str]],
    fye_month: int = 12,
    root: Path | None = None,
) -> FileResult:
    # same rules as import-archive: ticker from the file or folder name, calendar quarter from the name
    rel = str(path.relative_to(root)) if root else path.name
    index = {t: i for i, t in enumerate(sectors)}
    hit = ticker_from_name(rel, index)
    ticker = list(sectors)[hit] if hit is not None else None
    pe = period_from_name(rel)
    res = FileResult(rel, ticker, sectors.get(ticker or ""), pe.isoformat() if pe else None)
    meta = DocMeta(0, None, "supplemental", "eval", "application/pdf", path.name, pe, fye_month)
    try:
        out = parser.parse(path.read_bytes(), meta)
    except ParseError as e:
        res.status, res.error = "failed", str(e)[:300]
        return res
    res.pages = len({t.page_no for t in out.tables} | {i.page_no for i in out.items})
    res.tables = len(out.tables)
    res.tables_classified = sum(1 for t in out.tables if t.table_kind and t.table_kind != "other")
    res.items = len(out.items)
    res.items_quarantined = sum(1 for i in out.items if i.quarantined)
    res.records = len(out.records)
    res.records_quarantined = sum(1 for r in out.records if r.quarantined)
    res.kinds = dict(Counter(t.table_kind or "other" for t in out.tables))
    res.record_kinds = dict(Counter(r.record_kind for r in out.records if not r.quarantined))
    kinds = {(t.page_no, t.table_index): t.table_kind for t in out.tables}
    found = match_metrics(out.items, kinds, rules)
    res.metrics_found = sorted(found)
    want = core.get((res.sector or "").lower(), set()) - XBRL_FIRST
    res.core_expected = len(want)
    res.core_found = len(want & found)
    res.core_missing = sorted(want - found)
    if not out.items and not out.records:
        res.status = "no_values"
    return res


def evaluate_folder(
    folder: Path, universe_csv: Path | None = None, min_confidence: float = 0.80, limit: int | None = None
) -> tuple[list[FileResult], dict[str, Any]]:
    sectors: dict[str, str] = {}
    if universe_csv and universe_csv.exists():
        with open(universe_csv, encoding="utf-8-sig", newline="") as fh:
            sectors = {
                r["ticker"].strip().upper(): r.get("sector") or "" for r in csv.DictReader(fh) if r.get("ticker")
            }
    parser = SupplementalPdfParser(min_confidence=min_confidence)
    rules = load_rules()
    core = core_metrics_by_sector()
    files = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")
    if limit:
        files = files[:limit]
    results = [evaluate_file(p, parser, rules, sectors, core, root=folder) for p in files]
    return results, summarize(results)


def summarize(results: list[FileResult]) -> dict[str, Any]:
    ok = [r for r in results if r.status == "ok"]
    items = sum(r.items for r in results)
    recs = sum(r.records for r in results)
    tables = sum(r.tables for r in results)
    expected = sum(r.core_expected for r in results)
    kinds: Counter[str] = Counter()
    rkinds: Counter[str] = Counter()
    metric_files: Counter[str] = Counter()
    for r in results:
        kinds.update(r.kinds)
        rkinds.update(r.record_kinds)
        metric_files.update(r.metrics_found)
    return {
        "files": len(results),
        "files_with_values": len(ok),
        "files_failed": sum(1 for r in results if r.status == "failed"),
        "tables": tables,
        "tables_classified_pct": round(100 * sum(r.tables_classified for r in results) / tables, 1) if tables else None,
        "values": items,
        "values_quarantined_pct": round(100 * sum(r.items_quarantined for r in results) / items, 1) if items else None,
        "records": recs,
        "records_quarantined_pct": round(100 * sum(r.records_quarantined for r in results) / recs, 1) if recs else None,
        "core_metric_coverage_pct": round(100 * sum(r.core_found for r in results) / expected, 1) if expected else None,
        "table_kinds": dict(kinds.most_common()),
        "record_kinds": dict(rkinds.most_common()),
        "files_per_metric": dict(metric_files.most_common()),
    }


def write_results(results: list[FileResult], summary: dict[str, Any], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = [k for k in asdict(FileResult("x")) if k not in ("kinds", "record_kinds", "metrics_found")]
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([*cols, "table_kinds", "record_kinds", "metrics_found"])
        for r in results:
            d = asdict(r)
            w.writerow(
                [";".join(d[c]) if isinstance(d[c], list) else d[c] for c in cols]
                + [json.dumps(r.kinds, sort_keys=True), json.dumps(r.record_kinds, sort_keys=True)]
                + [";".join(r.metrics_found)]
            )
    out.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
