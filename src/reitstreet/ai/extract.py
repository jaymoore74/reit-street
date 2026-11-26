"""Read the numbers on a supplemental page with the local model, and check them.

The deterministic parser (parsers/pdf, v0.4.0) stays the system of record. The model is a
second reader for pages the parser cannot classify or reads with low confidence, and for
formats nobody has written rules for yet. Its output is never trusted as is:

- grounding: every number the model returns must appear on the page as printed; values
  that do not are dropped and counted (`ungrounded`), which catches invented numbers;
- scale: the model reports numbers as printed plus the page's stated scale, and the code,
  not the model, multiplies;
- agreement: where the parser read the same cell, the two are compared (evaluate.py).

The output format here is the one the fine-tuning set teaches (dataset.py), so a trained
model and this code agree on it exactly.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from importlib import resources
from typing import Any

from .llm import ChatClient, LLMError, parse_json

PAGE_CHARS = 12000  # longer pages are cut; supplemental tables fit easily

SCALES = {"units": Decimal(1), "thousands": Decimal(1000), "millions": Decimal(1_000_000), "billions": Decimal(10**9)}
UNITS = ("USD", "USD/shares", "pct", "x", "sf", "units", "acres", "count", "other")

SYSTEM = (
    "You read pages of REIT supplemental financial reports and return every number in their tables as JSON. "
    "Copy labels exactly as printed. Report each number as printed (keep the digits; parentheses mean negative) "
    "and give the page's stated scale separately. Do not compute, convert or invent anything. "
    "Skip page numbers, footnote markers and dates used as headings."
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "table_title": {"type": ["string", "null"]},
        "scale": {"type": "string", "enum": list(SCALES)},
        "values": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "column": {"type": ["string", "null"]},
                    "period_end": {"type": ["string", "null"]},
                    "segment": {"type": ["string", "null"]},
                    "printed": {"type": "string"},
                    "unit": {"type": "string", "enum": list(UNITS)},
                },
                "required": ["label", "printed", "unit"],
            },
        },
    },
    "required": ["scale", "values"],
}

INSTRUCTIONS = (
    "Return one JSON object with keys: table_title, scale (one of: units, thousands, millions, billions), and "
    "values: a list of {label, column, period_end (YYYY-MM-DD or null), segment (or null), printed, unit}. "
    "unit is one of: " + ", ".join(UNITS) + ". Percentages are pct; per-share amounts are USD/shares; "
    "ratios like 5.2x are x. Return only the JSON."
)


def user_prompt(page_text: str, *, ticker: str = "", period_end: str = "", page_no: int | None = None) -> str:
    head = []
    if ticker:
        head.append(f"Company: {ticker}")
    if period_end:
        head.append(f"Report period end: {period_end}")
    if page_no:
        head.append(f"Page: {page_no}")
    text = page_text if len(page_text) <= PAGE_CHARS else page_text[:PAGE_CHARS] + "\n[page cut]"
    return "\n".join([*head, INSTRUCTIONS, "", "PAGE TEXT:", text])


_NUM = re.compile(r"^\(?-?\$?\s*\(?([0-9][0-9,]*(?:\.[0-9]+)?|\.[0-9]+)\)?\s*(%|x)?\)?$")


def parse_printed(printed: str) -> Decimal | None:
    """'(1,234.5)' -> -1234.5, '$12.00' -> 12.00, '4.5%' -> 4.5, '—' -> None."""
    s = printed.strip().replace("−", "-").replace("$", "").replace(" ", "")
    if s in ("", "-", "--", "—", "–", "n/a", "N/A", "nm", "NM"):
        return None
    neg = (s.startswith("(") and s.endswith(")")) or s.startswith("-") or s.startswith("(-")
    core = s.strip("()-%xX")
    core = core.replace(",", "")
    try:
        v = Decimal(core)
    except InvalidOperation:
        return None
    return -v if neg else v


def _digits(s: str) -> str:
    return re.sub(r"[^0-9.]", "", s.replace(",", "")).strip(".")


def grounded(printed: str, page_text: str) -> bool:
    """True when the number's digits appear on the page as a whole number token."""
    d = _digits(printed)
    if not d:
        return False
    pat = r"(?<![0-9.])" + re.escape(d).replace(r"\.", r"\.") + r"(?![0-9])"
    flat = page_text.replace(",", "")
    if re.search(pat, flat):
        return True
    # 5.0 printed as 5.0% or written 5 on the page: compare numerically against page tokens
    try:
        want = Decimal(d)
    except InvalidOperation:
        return False
    for tok in re.findall(r"[0-9]+(?:\.[0-9]+)?", flat):
        try:
            if Decimal(tok) == want and len(tok.replace(".", "")) >= 1:
                return True
        except InvalidOperation:
            continue
    return False


@dataclass
class ExtractedValue:
    label: str
    printed: str
    value: Decimal | None
    unit: str
    column: str | None = None
    period_end: str | None = None
    segment: str | None = None
    grounded: bool = True
    metric_code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["value"] = None if self.value is None else format(self.value, "f")
        return d


@dataclass
class PageExtraction:
    page_no: int | None
    table_title: str | None
    scale: str
    values: list[ExtractedValue] = field(default_factory=list)
    ungrounded: int = 0
    invalid: int = 0
    model: str = ""
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "page_no": self.page_no,
            "table_title": self.table_title,
            "scale": self.scale,
            "values": [v.as_dict() for v in self.values],
            "ungrounded": self.ungrounded,
            "invalid": self.invalid,
            "model": self.model,
            "error": self.error,
        }


def scaled(value: Decimal | None, unit: str, scale: str) -> Decimal | None:
    """Apply the page scale to money and counts; never to percentages, ratios or per-share amounts."""
    if value is None:
        return None
    if unit in ("pct", "x", "USD/shares"):
        return value
    return value * SCALES.get(scale, Decimal(1))


def read_reply(
    obj: Any, page_text: str, *, page_no: int | None = None, keep_ungrounded: bool = False
) -> PageExtraction:
    if not isinstance(obj, dict) or not isinstance(obj.get("values"), list):
        raise LLMError("reply is not an object with a values list")
    scale = str(obj.get("scale") or "units").lower()
    if scale not in SCALES:
        scale = "units"
    out = PageExtraction(page_no=page_no, table_title=obj.get("table_title"), scale=scale)
    for v in obj["values"]:
        if not isinstance(v, dict) or not v.get("label") or v.get("printed") in (None, ""):
            out.invalid += 1
            continue
        printed = str(v["printed"])
        unit = str(v.get("unit") or "other")
        if unit not in UNITS:
            unit = "other"
        num = parse_printed(printed)
        if num is None:
            out.invalid += 1
            continue
        ok = grounded(printed, page_text)
        if not ok:
            out.ungrounded += 1
            if not keep_ungrounded:
                continue
        out.values.append(
            ExtractedValue(
                label=str(v["label"]).strip(),
                printed=printed,
                value=scaled(num, unit, scale),
                unit=unit,
                column=v.get("column"),
                period_end=v.get("period_end"),
                segment=v.get("segment"),
                grounded=ok,
            )
        )
    return out


def extract_page(
    client: ChatClient,
    page_text: str,
    *,
    ticker: str = "",
    period_end: str = "",
    page_no: int | None = None,
    rules: list[tuple[re.Pattern[str], re.Pattern[str] | None, str]] | None = None,
) -> PageExtraction:
    msgs = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user_prompt(page_text, ticker=ticker, period_end=period_end, page_no=page_no)},
    ]
    reply = client.chat(msgs, json_schema=SCHEMA)
    try:
        out = read_reply(parse_json(reply.text), page_text, page_no=page_no)
    except LLMError as e:
        return PageExtraction(page_no=page_no, table_title=None, scale="units", model=reply.model, error=str(e))
    out.model = reply.model
    if rules:
        for v in out.values:
            v.metric_code = map_label(v.label, rules)
    return out


# ----------------------------------------------------------------------------- mapping
def load_label_rules() -> list[tuple[re.Pattern[str], re.Pattern[str] | None, str]]:
    """Supplemental label rules from reference/data/metric_map.csv (the same rules build-metrics uses)."""
    text = resources.files("reitstreet.reference").joinpath("data", "metric_map.csv").read_text(encoding="utf-8")
    rows = [
        r for r in csv.DictReader(io.StringIO(text)) if r.get("source_type") == "supplemental" and r.get("label_regex")
    ]
    rows.sort(key=lambda r: int(r.get("priority") or 100))
    out = []
    for r in rows:
        try:
            inc = re.compile(r["label_regex"], re.I)
            exc = re.compile(r["exclude_regex"], re.I) if r.get("exclude_regex") else None
        except re.error:
            continue
        out.append((inc, exc, r["metric_code"]))
    return out


def map_label(label: str, rules: list[tuple[re.Pattern[str], re.Pattern[str] | None, str]]) -> str | None:
    for inc, exc, code in rules:
        if inc.search(label) and not (exc and exc.search(label)):
            return code
    return None


# ----------------------------------------------------------------------------- pages
def pdf_pages(data: bytes, pages: list[int] | None = None) -> dict[int, str]:
    """Text of each page (1-based), laid out so table columns stay apart."""
    import pdfplumber  # local import: heavy, only extraction needs it

    out: dict[int, str] = {}
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            if pages and i not in pages:
                continue
            out[i] = page.extract_text(layout=True) or ""
    return out


def dumps(results: list[PageExtraction]) -> str:
    return json.dumps([r.as_dict() for r in results], indent=2)
