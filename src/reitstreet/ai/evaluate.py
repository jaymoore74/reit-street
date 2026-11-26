"""Score a model on held-out REITs: `reitstreet-ai eval`.

Extraction (eval_extract.jsonl): the model reads each test page; a target value counts as
found when the model returns the same label (compared on letters and digits only) and the
same number after scale. Reported: recall (targets found), precision (model values that
match a target), number accuracy among label matches, ungrounded values (numbers not on
the page: hallucinations), JSON failures, seconds per page.

Questions (eval_qa.jsonl, including the gold file): the model answers through ask.py
against the warehouse; correct when the answer states the expected number (any common
formatting, to 3 significant figures or better), or says the data is not available when
none is expected. Gold questions are scored separately: they are hand-checked.

A run writes report.json and report.md next to the data, and `--baseline report.json`
prints the change against an earlier run, which is how a fine-tune is accepted or not.
Default acceptance gates (gates()): extraction recall >= 0.95, precision >= 0.97,
ungrounded <= 0.5%, question accuracy >= 0.90, gold accuracy = 1.0.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ..core.db import Db
from . import ask as askmod
from . import extract as ex
from .llm import ChatClient

GATES = {
    "extract.recall": (">=", 0.95),
    "extract.precision": (">=", 0.97),
    "extract.ungrounded_rate": ("<=", 0.005),
    "qa.accuracy": (">=", 0.90),
    "gold.accuracy": (">=", 1.0),
}


def norm_label(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def close(a: Decimal | None, b: Decimal | None, *, rel: Decimal = Decimal("0.0005")) -> bool:
    if a is None or b is None:
        return False
    if a == b:
        return True
    return abs(a - b) <= abs(b) * rel


def expected_values(target: dict[str, Any]) -> list[tuple[str, Decimal | None]]:
    scale = target.get("scale") or "units"
    out = []
    for v in target.get("values", []):
        num = ex.parse_printed(str(v.get("printed", "")))
        out.append((norm_label(v.get("label")), ex.scaled(num, v.get("unit", "other"), scale)))
    return out


@dataclass
class ExtractScore:
    pages: int = 0
    json_failures: int = 0
    targets: int = 0
    predicted: int = 0
    matched: int = 0
    label_matched: int = 0
    number_correct: int = 0
    ungrounded: int = 0
    seconds: float = 0.0
    worst: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, float | int]:
        n = max(self.pages, 1)
        return {
            "pages": self.pages,
            "json_failure_rate": round(self.json_failures / n, 4),
            "recall": round(self.matched / self.targets, 4) if self.targets else 0.0,
            "precision": round(self.matched / self.predicted, 4) if self.predicted else 0.0,
            "number_accuracy": round(self.number_correct / self.label_matched, 4) if self.label_matched else 0.0,
            "ungrounded_rate": round(self.ungrounded / max(self.predicted + self.ungrounded, 1), 4),
            "seconds_per_page": round(self.seconds / n, 2),
        }


def score_page(pred: ex.PageExtraction, target: dict[str, Any]) -> tuple[int, int, int, int, int]:
    """(targets, predicted, matched, label_matched, number_correct) for one page."""
    exp = expected_values(target)
    preds = [(norm_label(v.label), v.value) for v in pred.values]
    used = [False] * len(preds)
    matched = label_matched = number_correct = 0
    for lab, val in exp:
        hit_label = None
        for i, (pl, pv) in enumerate(preds):
            if used[i] or pl != lab:
                continue
            if close(pv, val):
                used[i] = True
                matched += 1
                label_matched += 1
                number_correct += 1
                hit_label = None
                break
            hit_label = i
        else:
            if hit_label is not None:
                used[hit_label] = True
                label_matched += 1
    return len(exp), len(preds), matched, label_matched, number_correct


def eval_extract(client: ChatClient, cases: Iterable[dict[str, Any]], *, limit: int | None = None) -> ExtractScore:
    sc = ExtractScore()
    for i, c in enumerate(cases):
        if limit is not None and i >= limit:
            break
        t0 = time.monotonic()
        pred = ex.extract_page(
            client,
            c["page_text"],
            ticker=c.get("ticker", ""),
            period_end=c.get("period_end", ""),
            page_no=c.get("page_no"),
        )
        sc.seconds += time.monotonic() - t0
        sc.pages += 1
        if pred.error:
            sc.json_failures += 1
        t, p, m, lm, nc = score_page(pred, c["expected"])
        sc.targets += t
        sc.predicted += p
        sc.matched += m
        sc.label_matched += lm
        sc.number_correct += nc
        sc.ungrounded += pred.ungrounded
        recall = m / t if t else 1.0
        sc.worst.append(
            {
                "ticker": c.get("ticker"),
                "doc_id": c.get("doc_id"),
                "page_no": c.get("page_no"),
                "recall": round(recall, 3),
                "ungrounded": pred.ungrounded,
                "error": pred.error,
            }
        )
    sc.worst = sorted(sc.worst, key=lambda w: (w["recall"], -w["ungrounded"]))[:15]
    return sc


# ------------------------------------------------------------------------------- questions
_NOT_AVAILABLE = re.compile(
    r"\b(no|not|isn't|is not|unavailable|n/a|does not|doesn't)\b.*\b(available|report|have|data|warehouse|found)", re.I
)
_NUMS = re.compile(r"-?\$?\(?[0-9][0-9,]*(?:\.[0-9]+)?\)?\s*(?:%|x|billion|million|thousand|bn|mm|m|k)?", re.I)
_MULT = {"billion": 10**9, "bn": 10**9, "million": 10**6, "mm": 10**6, "m": 10**6, "thousand": 1000, "k": 1000}


def _numbers(text: str) -> list[tuple[Decimal, Decimal]]:
    """(value, half a unit of the last digit written) for each number in the text."""
    out = []
    for tok in _NUMS.findall(text):
        t = tok.strip().lower()
        neg = t.startswith("-") or t.startswith("(")
        m = re.search(r"(billion|million|thousand|bn|mm|m|k)$", t)
        core = re.sub(r"[^0-9.]", "", t)
        if not core or core == ".":
            continue
        try:
            v = Decimal(core)
        except InvalidOperation:
            continue
        mult = _MULT[m.group(1)] if m else Decimal(1)
        exp = v.as_tuple().exponent
        half = Decimal(10) ** (exp if isinstance(exp, int) else 0) * mult / 2
        out.append(((-v if neg else v) * mult, half))
    return out


def numbers_in(text: str) -> list[Decimal]:
    return [v for v, _ in _numbers(text)]


def states_value(answer: str, expected: Decimal) -> bool:
    """The answer gives the expected number, rounded correctly to the digits it shows and
    within 0.5%: "$467.8 million" and "$468 million" pass for 467,812,000; "$470.0 million"
    and "$0.5 billion" do not."""
    if expected == 0:
        return any(n == 0 for n in numbers_in(answer))
    for n, half in _numbers(answer):
        for cand in (n, -n):
            diff = abs(cand - expected)
            if diff <= half * Decimal("1.0001") and diff <= abs(expected) * Decimal("0.005"):
                return True
    return False


@dataclass
class QaScore:
    questions: int = 0
    correct: int = 0
    gold: int = 0
    gold_correct: int = 0
    refused_sql: int = 0
    seconds: float = 0.0
    misses: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "questions": self.questions,
            "accuracy": round(self.correct / self.questions, 4) if self.questions else 0.0,
            "gold_questions": self.gold,
            "gold_accuracy": round(self.gold_correct / self.gold, 4) if self.gold else None,
            "refused_queries": self.refused_sql,
            "seconds_per_question": round(self.seconds / max(self.questions + self.gold, 1), 2),
        }


def eval_qa(db: Db, client: ChatClient, cases: Iterable[dict[str, Any]], *, limit: int | None = None) -> QaScore:
    sc = QaScore()
    for i, c in enumerate(cases):
        if limit is not None and i >= limit:
            break
        t0 = time.monotonic()
        a = askmod.ask(db, client, c["question"])
        sc.seconds += time.monotonic() - t0
        sc.refused_sql += sum(1 for e in a.errors if "refused" in e)
        if c.get("expected_value") is None:
            ok = bool(_NOT_AVAILABLE.search(a.answer)) and not numbers_in(a.answer.replace(c["ticker"], ""))
        else:
            ok = states_value(a.answer, Decimal(str(c["expected_value"])))
        if c.get("gold"):
            sc.gold += 1
            sc.gold_correct += int(ok)
        else:
            sc.questions += 1
            sc.correct += int(ok)
        if not ok and len(sc.misses) < 25:
            sc.misses.append({"question": c["question"], "expected": c.get("expected_value"), "answer": a.answer[:400]})
    return sc


# ------------------------------------------------------------------------------- report
def read_cases(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def gates(summary: dict[str, Any], rules: dict[str, tuple[str, float]] = GATES) -> dict[str, bool]:
    out = {}
    for key, (op, thr) in rules.items():
        sect, name = key.split(".")
        v = summary.get("qa", {}).get("gold_accuracy") if sect == "gold" else summary.get(sect, {}).get(name)
        if v is None:
            continue
        out[key] = v >= thr if op == ">=" else v <= thr
    return out


def compare(now: dict[str, Any], before: dict[str, Any]) -> list[str]:
    lines = []
    for sect in ("extract", "qa"):
        for k, v in now.get(sect, {}).items():
            b = before.get(sect, {}).get(k)
            if isinstance(v, (int, float)) and isinstance(b, (int, float)):
                lines.append(f"{sect}.{k}: {b} -> {v} ({v - b:+.4g})")
    return lines


def to_markdown(rep: dict[str, Any]) -> str:
    lines = [f"# Model evaluation: {rep.get('model') or 'unnamed'}", "", f"Data: {rep.get('data')}", ""]
    for sect in ("extract", "qa"):
        if sect in rep:
            lines.append(f"## {sect}")
            lines += [f"- {k}: {v}" for k, v in rep[sect].items()]
            lines.append("")
    if rep.get("gates"):
        lines.append("## gates")
        lines += [f"- {k}: {'pass' if ok else 'FAIL'}" for k, ok in rep["gates"].items()]
    return "\n".join(lines) + "\n"
