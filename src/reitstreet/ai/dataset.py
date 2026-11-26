"""Training and evaluation sets for the local model: `reitstreet-ai build-dataset`.

Two skills, both taught in the exact formats the runtime uses:

1. extract: a supplemental page's text in, every table number as JSON out (extract.py).
   Targets come from the deterministic parser's line items (stg_doc_line_item) for the
   document's current parse, kept only when the parser was confident and the printed
   number is on the page text the model will see. Pages with too many values are left
   out rather than cut, so the model never learns to stop early.

2. ask: a question in, JSON moves out (ask.py protocol): a read-only query on dl_metrics,
   the real result rows, then an answer that states the number, period and source. The
   queries are templates run against the warehouse while building, so every target is a
   true answer. Some questions ask for data that is not there, to teach "not available"
   instead of a guess.

Splits are by company, not by row: about 10% of REITs are test and 10% valid, chosen by a
hash of the ticker, so the score measures REITs the model has never seen. A hand-checked
gold file (--gold) always goes to test.

Output (mlx-lm chat format, one {"messages": [...]} per line):
  train.jsonl valid.jsonl test.jsonl   what mlx_lm.lora reads
  eval_extract.jsonl eval_qa.jsonl     test cases with expected values, for evaluate.py
  manifest.json                        counts, settings, source database, file hashes

Everything stays under [ai] dir (data/ai, git-ignored): it is warehouse content.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..core.db import Db
from ..core.rawstore import RawStore
from . import ask as askmod
from . import extract as ex

SEED = 20261126


def split_of(ticker: str, *, test_pct: int = 10, valid_pct: int = 10) -> str:
    b = int(hashlib.sha256(ticker.upper().encode()).hexdigest(), 16) % 100
    if b < test_pct:
        return "test"
    if b < test_pct + valid_pct:
        return "valid"
    return "train"


def _plain(v: Any) -> Any:
    return askmod._plain(v)


# ------------------------------------------------------------------------------ extract
def unit_of(unit_hint: str | None, raw_text: str | None, label: str) -> str:
    h = (unit_hint or "").lower()
    raw = (raw_text or "").strip().lower()
    lab = label.lower()
    if "%" in h or raw.endswith("%") or raw.endswith("%)"):
        return "pct"
    if h in ("x",) or raw.endswith("x") or raw.endswith("x)"):
        return "x"
    if "per share" in lab or "per unit" in lab or "/share" in h:
        return "USD/shares"
    if "sf" in h or "square feet" in h or "sq. ft" in h:
        return "sf"
    if "unit" in h and "$" not in h:
        return "units"
    if "acre" in h:
        return "acres"
    if "$" in h or "usd" in h or "dollar" in h:
        return "USD"
    return "other"


def scale_of(hints: Iterable[str | None]) -> str:
    votes: dict[str, int] = defaultdict(int)
    for h in hints:
        t = (h or "").lower()
        for k in ("billions", "millions", "thousands"):
            if k[:-1] in t:
                votes[k] += 1
                break
        else:
            votes["units"] += 1
    return max(votes.items(), key=lambda kv: kv[1])[0] if votes else "units"


@dataclass
class BuildStats:
    pages_seen: int = 0
    pages_kept: int = 0
    pages_too_big: int = 0
    pages_no_text: int = 0
    values_kept: int = 0
    values_dropped_low_conf: int = 0
    values_dropped_ungrounded: int = 0
    qa_examples: int = 0
    qa_missing_examples: int = 0
    gold_examples: int = 0
    by_split: dict[str, int] = field(default_factory=lambda: defaultdict(int))


def extraction_examples(
    db: Db,
    store: RawStore,
    stats: BuildStats,
    *,
    min_confidence: float = 0.9,
    max_values: int = 120,
    min_values: int = 3,
    tickers: list[str] | None = None,
) -> Iterable[tuple[str, dict[str, Any], dict[str, Any]]]:
    """(ticker, chat record, eval case) for each usable supplemental page."""
    params: dict[str, Any] = {}
    where = ""
    if tickers:
        where = " AND c.ticker IN (" + ",".join(f":t{i}" for i in range(len(tickers))) + ")"
        params = {f"t{i}": t for i, t in enumerate(tickers)}
    docs = db.query(
        f"""SELECT d.doc_id, d.sha256, d.current_parse_id, d.period_end_date, c.ticker
            FROM stg_document d JOIN ref_company c ON c.company_id = d.company_id
            WHERE d.doc_kind = 'supplemental' AND d.current_parse_id IS NOT NULL AND d.sha256 IS NOT NULL
              AND d.status IN ('parsed', 'needs_review'){where}
            ORDER BY c.ticker, d.period_end_date, d.doc_id""",
        params,
    )
    for d in docs:
        items = db.query(
            """SELECT i.page_no, i.row_label, i.col_label, i.raw_text, i.value_numeric, i.scale_hint, i.unit_hint,
                      i.period_end, i.segment_label, i.confidence, t.title
               FROM stg_doc_line_item i LEFT JOIN stg_doc_table t ON t.table_id = i.table_id
               WHERE i.parse_id = :p ORDER BY i.page_no, i.table_id, i.row_index, i.col_index""",
            {"p": d["current_parse_id"]},
        )
        if not items:
            continue
        by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for it in items:
            by_page[int(it["page_no"])].append(it)
        try:
            texts = ex.pdf_pages(store.get(d["sha256"]), sorted(by_page))
        except Exception:  # unreadable bytes are the parser's problem, not ours
            stats.pages_no_text += len(by_page)
            continue
        period = d["period_end_date"].isoformat() if d["period_end_date"] else ""
        for page_no, rows in sorted(by_page.items()):
            stats.pages_seen += 1
            text = texts.get(page_no, "")
            if len(text.strip()) < 40:
                stats.pages_no_text += 1
                continue
            vals = []
            for r in rows:
                conf = float(r["confidence"]) if r["confidence"] is not None else 0.0
                raw = (r["raw_text"] or "").strip()
                if conf < min_confidence or not raw or ex.parse_printed(raw) is None:
                    stats.values_dropped_low_conf += 1
                    continue
                if not ex.grounded(raw, text):
                    stats.values_dropped_ungrounded += 1
                    continue
                vals.append(
                    {
                        "label": r["row_label"],
                        "column": r["col_label"],
                        "period_end": r["period_end"].isoformat() if r["period_end"] else None,
                        "segment": r["segment_label"],
                        "printed": raw,
                        "unit": unit_of(r["unit_hint"], raw, r["row_label"]),
                    }
                )
            if len(vals) < min_values:
                continue
            if len(vals) > max_values:
                stats.pages_too_big += 1
                continue
            titles = [r["title"] for r in rows if r["title"]]
            target = {
                "table_title": titles[0] if titles else None,
                "scale": scale_of(r["scale_hint"] for r in rows),
                "values": vals,
            }
            prompt = ex.user_prompt(text, ticker=d["ticker"], period_end=period, page_no=page_no)
            record = {
                "messages": [
                    {"role": "system", "content": ex.SYSTEM},
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": json.dumps(target, ensure_ascii=False)},
                ]
            }
            case = {
                "kind": "extract",
                "ticker": d["ticker"],
                "doc_id": d["doc_id"],
                "page_no": page_no,
                "period_end": period,
                "page_text": text,
                "expected": target,
            }
            stats.pages_kept += 1
            stats.values_kept += len(vals)
            yield d["ticker"], record, case


# ------------------------------------------------------------------------------ ask
def fmt_value(v: Decimal | float | int, unit: str) -> str:
    d = Decimal(str(v))
    if unit == "pct":
        return f"{d:.2f}%"
    if unit == "USD/shares":
        return f"${d:,.2f} per share"
    if unit == "USD":
        a = abs(d)
        if a >= 10**9:
            return f"${d / 10**9:,.2f} billion"
        if a >= 10**6:
            return f"${d / 10**6:,.1f} million"
        return f"${d:,.0f}"
    if d == d.to_integral():
        return f"{d:,.0f} {unit}".strip()
    return f"{d:,.2f} {unit}".strip()


def _period_words(r: dict[str, Any]) -> str:
    pe = r["period_end"]
    pe_s = pe.isoformat() if hasattr(pe, "isoformat") else str(pe)
    pt = r["period_type"]
    if pt == "Q":
        return f"the quarter ended {pe_s}"
    if pt == "FY":
        return f"fiscal year {r['fiscal_year']} (ended {pe_s})"
    if pt == "YTD":
        return f"the year to date ended {pe_s}"
    return f"{pe_s}"


def _source_words(r: dict[str, Any]) -> str:
    src = r["source_type"]
    page = f", page {r['source_page_no']}" if r.get("source_page_no") else ""
    where = r.get("source_document_url") or r.get("source_filing_url") or ""
    return f"{src}{page}" + (f" ({where})" if where else "")


Q_PERIOD = [
    "What was {ticker}'s {name} for {period}?",
    "{ticker} {name}, {period}?",
    "Give me {name} for {ticker} for {period}.",
]
Q_LATEST = [
    "What is {ticker}'s latest {name}?",
    "Latest {name} for {ticker}?",
    "Most recent {name} reported by {ticker}?",
]
SQL_PERIOD = (
    "SELECT metric_code, period_type, period_end, segment, value, unit, source_type, source_page_no, "
    "source_document_url, source_filing_url FROM dl_metrics WHERE ticker = '{ticker}' AND metric_code = '{metric}' "
    "AND segment = 'Total' AND period_type = '{ptype}' AND period_end = '{pe}' AND is_preferred = 1"
)
SQL_LATEST = (
    "SELECT metric_code, period_type, period_end, segment, value, unit, source_type, source_page_no, "
    "source_document_url, source_filing_url FROM dl_metrics WHERE ticker = '{ticker}' AND metric_code = '{metric}' "
    "AND segment = 'Total' AND is_preferred = 1 ORDER BY period_end DESC LIMIT 1"
)


def _convo(
    system: str, question: str, sql: str, rows: list[dict[str, Any]], answer: str, sources: list[str]
) -> dict[str, Any]:
    return {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": question},
            {"role": "assistant", "content": json.dumps({"action": "sql", "sql": sql})},
            {"role": "user", "content": "RESULT " + askmod.rows_json(rows)},
            {"role": "assistant", "content": json.dumps({"action": "answer", "answer": answer, "sources": sources})},
        ]
    }


def qa_examples(
    db: Db, stats: BuildStats, *, per_company: int = 40, tickers: list[str] | None = None, rng: random.Random
) -> Iterable[tuple[str, dict[str, Any], dict[str, Any]]]:
    system = askmod.SYSTEM.format(schema=askmod.schema_text(db))
    companies = [r["ticker"] for r in db.query("SELECT DISTINCT ticker FROM dl_metrics ORDER BY ticker")]
    if tickers:
        companies = [t for t in companies if t in set(tickers)]
    metric_names = {r["metric_code"]: r["name"] for r in db.query("SELECT metric_code, name FROM ref_metric")}
    for t in companies:
        rows = db.query(
            """SELECT metric_code, metric_name, period_type, period_end, fiscal_year, segment, value, unit,
                      source_type, source_page_no, source_document_url, source_filing_url
               FROM dl_metrics WHERE ticker = :t AND is_preferred = 1 AND segment = 'Total'
                 AND period_type IN ('Q', 'FY', 'INSTANT')
               ORDER BY metric_code, period_end""",
            {"t": t},
        )
        if not rows:
            continue
        rng.shuffle(rows)
        have = {r["metric_code"] for r in rows}
        n = 0
        latest_done: set[str] = set()
        for r in rows:
            if n >= per_company:
                break
            name = (r["metric_name"] or r["metric_code"]).lower()
            if r["metric_code"] not in latest_done and rng.random() < 0.3:
                latest_done.add(r["metric_code"])
                sql = SQL_LATEST.format(ticker=t, metric=r["metric_code"])
                q = rng.choice(Q_LATEST).format(ticker=t, name=name)
            else:
                pe = r["period_end"].isoformat()
                sql = SQL_PERIOD.format(ticker=t, metric=r["metric_code"], ptype=r["period_type"], pe=pe)
                q = rng.choice(Q_PERIOD).format(ticker=t, name=name, period=_period_words(r))
            res = db.query(sql)
            if not res:
                continue
            top = res[0]
            ans = (
                f"{t} {name}: {fmt_value(top['value'], top['unit'])} "
                f"for {_period_words({**top, 'fiscal_year': r['fiscal_year']})}. "
                f"Source: {_source_words(top)}."
            )
            src = [_source_words(top)]
            yield (
                t,
                _convo(system, q, sql, res, ans, src),
                {
                    "kind": "qa",
                    "ticker": t,
                    "question": q,
                    "metric_code": r["metric_code"],
                    "expected_value": format(Decimal(str(top["value"])), "f"),
                    "unit": top["unit"],
                    "period_end": top["period_end"].isoformat(),
                },
            )
            n += 1
            stats.qa_examples += 1
        # one "not available" example per company: a metric it does not report
        missing = sorted(set(metric_names) - have)
        if missing:
            m = rng.choice(missing)
            name = metric_names[m].lower()
            sql = SQL_LATEST.format(ticker=t, metric=m)
            q = rng.choice(Q_LATEST).format(ticker=t, name=name)
            ans = (
                f"The warehouse has no {name} for {t}. "
                "It is not reported in the filings or supplementals loaded so far."
            )
            stats.qa_missing_examples += 1
            yield (
                t,
                _convo(system, q, sql, [], ans, []),
                {
                    "kind": "qa",
                    "ticker": t,
                    "question": q,
                    "metric_code": m,
                    "expected_value": None,
                    "unit": None,
                    "period_end": None,
                },
            )


def gold_cases(path: Path) -> list[dict[str, Any]]:
    """Hand-checked values: one JSON object per line with ticker, metric_code, period_end, value, unit,
    and optional question and source. Lines starting with # are comments."""
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        g = json.loads(s)
        for k in ("ticker", "metric_code", "period_end", "value", "unit"):
            if k not in g:
                raise ValueError(f"{path}:{n}: missing {k}")
        out.append(
            {
                "kind": "qa",
                "gold": True,
                "ticker": g["ticker"].upper(),
                "question": g.get("question")
                or f"What was {g['ticker'].upper()}'s {g['metric_code'].lower().replace('_', ' ')} "
                f"for the period ended {g['period_end']}?",
                "metric_code": g["metric_code"],
                "expected_value": str(g["value"]),
                "unit": g["unit"],
                "period_end": g["period_end"],
                "source": g.get("source"),
            }
        )
    return out


# ------------------------------------------------------------------------------ build
def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(
    db: Db,
    store: RawStore,
    out_dir: Path,
    *,
    min_confidence: float = 0.9,
    max_values: int = 120,
    qa_per_company: int = 40,
    tickers: list[str] | None = None,
    gold: Path | None = None,
    skills: tuple[str, ...] = ("extract", "qa"),
    seed: int = SEED,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)  # noqa: S311  (reproducible sampling, not security)
    stats = BuildStats()
    split_rows: dict[str, list[dict[str, Any]]] = {"train": [], "valid": [], "test": []}
    cases: dict[str, list[dict[str, Any]]] = {"extract": [], "qa": []}
    sources: list[Iterable[tuple[str, dict[str, Any], dict[str, Any]]]] = []
    if "extract" in skills:
        sources.append(
            extraction_examples(db, store, stats, min_confidence=min_confidence, max_values=max_values, tickers=tickers)
        )
    if "qa" in skills:
        sources.append(qa_examples(db, stats, per_company=qa_per_company, tickers=tickers, rng=rng))
    for src in sources:
        for ticker, record, case in src:
            sp = split_of(ticker)
            split_rows[sp].append(record)
            stats.by_split[sp] += 1
            if sp == "test":
                cases[case["kind"]].append(case)
    if gold:
        g = gold_cases(gold)
        cases["qa"] += g
        stats.gold_examples = len(g)
    for sp, rows in split_rows.items():
        rng.shuffle(rows)
        with (out_dir / f"{sp}.jsonl").open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    for kind, rows in cases.items():
        with (out_dir / f"eval_{kind}.jsonl").open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    db_name = db.scalar("SELECT DATABASE()")
    manifest = {
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "database": db_name,
        "seed": seed,
        "settings": {
            "min_confidence": min_confidence,
            "max_values_per_page": max_values,
            "qa_per_company": qa_per_company,
            "tickers": tickers,
            "skills": list(skills),
            "gold": str(gold) if gold else None,
        },
        "stats": {k: (dict(v) if isinstance(v, defaultdict) else v) for k, v in stats.__dict__.items()},
        "files": {
            p.name: {"sha256": _sha(p), "lines": sum(1 for _ in p.open(encoding="utf-8"))}
            for p in sorted(out_dir.glob("*.jsonl"))
        },
        "split_rule": "sha256(ticker) % 100: <10 test, <20 valid, else train",
    }
    if not split_rows["valid"] and split_rows["train"]:
        manifest["warning"] = "no validation rows (too few companies); mlx_lm.lora needs valid.jsonl"
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return manifest
