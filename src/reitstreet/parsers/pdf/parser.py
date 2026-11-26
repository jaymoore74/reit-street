"""The supplemental PDF parser: page geometry -> classified tables -> values and records.

For every table it reads, it stores the table (kind, orientation, scale, headers, total
check) and either
  - line items (series and segment tables): one per cell, with period, unit, scale,
    column role (value / change / share), bounding box and a confidence score, or
  - records (property, tenant, development, debt, lease expiration tables): one per row,
    with typed fields.
Anything under the confidence bar is kept but quarantined, so the warehouse ignores it
and an analyst can review it. See docs/adr/0010-supplemental-pdf-parser.md.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from datetime import date
from decimal import Decimal

from ...core.errors import ParseError
from ..documents import DocMeta, Override, ParsedItem, ParsedRecord, ParsedTable, ParseResult
from .classify import RECORD_KINDS, Classification, classify, clean_label
from .layout import Table, TableRow, page_words, tables_on_page
from .periods import Period, column_role, parse_period, table_default_period
from .records import jsonable, read_records
from .values import Scale, apply_scale, column_scale, read_number, read_scale, unit_kind

VERSION = "1.0.0"
PER_SHARE_RX = re.compile(r"share|\bunit\b(?! (count|mix))|\beps\b|dividend", re.I)


def _unit_hint(kind: str, label: str, header: str) -> str | None:
    if kind == "usd_unit" and PER_SHARE_RX.search(f"{label} {header}"):
        return "USD/shares"
    return UNIT_HINT.get(kind)


UNIT_HINT = {
    "money": "USD",
    "usd_unit": "USD/unit",
    "usd_psf": "USD/sf",
    "pct": "pct",
    "ratio": "x",
    "count": "count",
    "sf": "sf",
    "shares": "shares",
    "number": None,
}
D3 = Decimal("0.001")


def _conf(x: float) -> Decimal:
    return Decimal(str(max(0.0, min(0.999, x)))).quantize(D3)


class SupplementalPdfParser:
    name = "supplemental_pdf"
    version = VERSION

    def __init__(self, min_confidence: float = 0.80, max_pages: int = 400) -> None:
        self.min_confidence = min_confidence
        self.max_pages = max_pages

    def can_parse(self, doc: DocMeta) -> bool:
        return (doc.mime_type or "").lower() == "application/pdf" or (doc.original_filename or "").lower().endswith(
            ".pdf"
        )

    def config_hash(self, doc: DocMeta) -> str:
        cfg = {
            "v": VERSION,
            "min_conf": self.min_confidence,
            "overrides": [asdict(o) for o in doc.overrides],
        }
        return hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()

    # ------------------------------------------------------------------ entry point
    def parse(self, data: bytes, doc: DocMeta) -> ParseResult:
        if not data.startswith(b"%PDF"):
            raise ParseError("not a PDF (missing %PDF header)")
        res = ParseResult(config_hash=self.config_hash(doc))
        min_conf = self.min_confidence
        for o in doc.overrides:
            if o.action == "min_confidence" and "value" in o.params:
                min_conf = float(o.params["value"])
        pages = list(page_words(data, self.max_pages))
        doc_scale = Scale(None, None, None)
        for _p, _w, _words, text in pages:
            m = _DOC_SCALE_RX.search(text)
            if m:
                doc_scale = read_scale(m.group(0))
                if doc_scale.text:
                    break
        for page_no, _width, words, text in pages:
            page_scale, page_source = read_scale(_first_scale_text(text)), "page"
            if not page_scale.text and doc_scale.text:
                page_scale, page_source = doc_scale, "document"
            for t in tables_on_page(page_no, words, text):
                self._table(res, t, doc, page_scale, min_conf, page_source)
        return res

    # ------------------------------------------------------------------ one table
    def _table(
        self, res: ParseResult, t: Table, doc: DocMeta, page_scale: Scale, min_conf: float, page_source: str = "page"
    ) -> None:
        title = t.title or t.page_heading or ""
        ovs = [o for o in doc.overrides if o.applies(t.page_no, f"{title} {' '.join(t.title_lines)}")]
        if any(o.action == "skip" for o in ovs):
            return
        cls = classify(t, doc.period_end_date, doc.fiscal_year_end_month)
        for o in ovs:
            if o.action == "force_kind":
                cls = Classification(
                    o.params.get("kind", cls.kind),
                    o.params.get("orientation", cls.orientation),
                    1.0,
                    cls.period_cols,
                    cls.value_cols,
                )

        scale, scale_source = read_scale(t.scale_text), "table"
        if (
            scale.text
            and t.scale_text
            and not re.search(re.escape(t.scale_text[:20]), " ".join(t.title_lines + t.header_lines) + t.context_text)
        ):
            scale_source = "page"
        if scale.money is None and scale.sf is None:
            scale, scale_source = (page_scale, page_source) if page_scale.text else (Scale(None, None, None), "none")
        for o in ovs:
            if o.action == "scale":
                money = o.params.get("money")
                sf = o.params.get("sf")
                scale = Scale(
                    Decimal(str(money)) if money is not None else scale.money,
                    Decimal(str(sf)) if sf is not None else scale.sf,
                    f"override {o.override_id}",
                )
                scale_source = "override"

        # the date a table describes, when its columns are not periods: "Debt Summary as of September 30, 2026"
        default_p, default_c = table_default_period(
            " ".join(t.title_lines), doc.period_end_date, doc.fiscal_year_end_month
        )
        if default_p is None and t.page_heading:
            default_p, default_c = table_default_period(t.page_heading, doc.period_end_date, doc.fiscal_year_end_month)
            default_c *= 0.9
        if cls.orientation == "series":
            ends = [
                p.end
                for c in t.columns
                if (p := parse_period(c.header, doc.period_end_date, doc.fiscal_year_end_month)[0])
            ]
            as_of = max(ends) if ends else doc.period_end_date
        else:
            as_of = default_p.end if default_p else doc.period_end_date

        pt = ParsedTable(
            page_no=t.page_no,
            table_index=t.index,
            title=(t.title or None) and t.title[:512],
            section=(t.page_heading or None) and t.page_heading[:255],
            bbox=t.bbox,
            table_kind=cls.kind,
            orientation=cls.orientation,
            kind_score=_conf(cls.score),
            scale_text=scale.text[:120] if scale.text else None,
            scale_factor=scale.money,
            scale_source=scale_source,
            headers=[c.header for c in t.columns],
            n_rows=len(t.rows),
            n_cols=len(t.columns),
            as_of=as_of,
        )
        res.tables.append(pt)
        ctx = _Ctx(t, cls, scale, scale_source, doc, default_p, default_c, min_conf)
        if cls.orientation == "records" and cls.kind in RECORD_KINDS:
            res.records.extend(self._records(ctx, as_of))
        else:
            items = self._items(ctx)
            pt.total_check = _total_check(t, items)
            if pt.total_check == "pass":
                for i in items:
                    i.confidence = _conf(float(i.confidence or 0) + 0.04)
            for i in items:
                _quarantine(i, min_conf)
            res.items.extend(items)

    # ------------------------------------------------------------------ series and segments
    def _items(self, c: _Ctx) -> list[ParsedItem]:
        t, cls = c.t, c.cls
        segments = cls.orientation == "segments"
        col_periods: dict[int, tuple[Period | None, float]] = {}
        col_roles: dict[int, str] = {}
        for col in t.columns:
            col_roles[col.index] = column_role(col.header)
            col_periods[col.index] = parse_period(col.header, c.doc.period_end_date, c.doc.fiscal_year_end_month)
        # a change or share column belongs to the latest period among the value columns to its left
        for col in t.columns:
            if col_roles[col.index] != "value" and col_periods[col.index][0] is None:
                left = [
                    col_periods[k] for k in range(1, col.index) if col_roles.get(k) == "value" and col_periods[k][0]
                ]
                if left:
                    best = max(left, key=lambda pc: pc[0].end if pc[0] else date.min)
                    col_periods[col.index] = (best[0], best[1] * 0.9)
        money_context = (c.scale.money is not None) or bool(re.search(r"dollars|\$|usd", c.scale.text or "", re.I))
        out: list[ParsedItem] = []
        for row in t.rows:
            label = clean_label(row.label)
            if not row.cells:
                continue
            row_dollar = any("$" in cell.text for cell in row.cells.values())
            for col in t.columns:
                cell = row.cells.get(col.index)
                if cell is None:
                    continue
                num = read_number(cell.text)
                header = clean_label(col.header)
                period, pconf = col_periods[col.index]
                if segments and period is None:
                    period, pconf = c.default_p, c.default_c * 0.95
                    if period is None and c.doc.period_end_date is not None:
                        period, pconf = Period(None, c.doc.period_end_date, "INSTANT"), 0.6
                metric_label = header if segments else label
                kind = unit_kind(metric_label, header if not segments else label, num, money_context and not segments)
                if (
                    segments
                    and kind == "number"
                    and (num.dollar_sign or re.search(r"\$|noi|rent|revenue|cost|value|ebitda", header, re.I))
                ):
                    kind = "money"
                if row_dollar and kind == "number":
                    kind = "money"
                cs = column_scale(col.header)
                sc = Scale(
                    cs.money if cs and cs.money else c.scale.money,
                    cs.sf if cs and cs.sf else c.scale.sf,
                    c.scale.text or (cs.text if cs else None),
                )
                value = num.value
                scale_known = True
                if value is not None:
                    if kind == "pct" and num.bps:
                        value = value / 100
                    value, scale_known = apply_scale(value, kind, sc)
                conf = self._confidence(
                    c, row, col.index, num.clean, value is not None, label, pconf, kind, scale_known, cs is not None
                )
                item = ParsedItem(
                    page_no=t.page_no,
                    table_index=t.index,
                    row_index=row.index,
                    col_index=col.index,
                    row_label=row.label.strip()[:512] or "(blank)",
                    col_label=col.header[:255] or None,
                    raw_text=cell.text[:255],
                    value_numeric=value.quantize(Decimal("0.000001"))
                    if value is not None and abs(value) < Decimal("1e21")
                    else None,
                    scale_hint=(sc.text or None) and (sc.text or "")[:32],
                    unit_hint=_unit_hint(kind, label, header),
                    period_start=period.start if period else None,
                    period_end=period.end if period else None,
                    segment_label=label[:255] if segments else None,
                    confidence=_conf(conf),
                    metric_label=metric_label[:512],
                    group_label=(clean_label(row.group)[:255] if row.group else None),
                    col_role=col_roles[col.index],
                    period_type=period.kind if period else None,
                    bbox=cell.bbox,
                )
                reasons = []
                if value is None and not num.clean:
                    reasons.append("unreadable number")
                if period is None:
                    reasons.append("no period")
                if col.index in row.ambiguous_cols:
                    reasons.append("ambiguous column")
                if not label:
                    reasons.append("no row label")
                if not scale_known and kind in ("money", "shares"):
                    reasons.append("scale unknown")
                item.quarantine_reason = "; ".join(reasons) or None
                out.append(item)
        return out

    def _confidence(
        self,
        c: _Ctx,
        row: TableRow,
        col_index: int,
        clean: bool,
        has_value: bool,
        label: str,
        pconf: float,
        kind: str,
        scale_known: bool,
        col_scaled: bool,
    ) -> float:
        x = 0.55
        x += 0.22 * pconf
        x += (
            0.08
            if (scale_known and c.scale_source in ("table", "override"))
            or kind not in ("money", "shares")
            or col_scaled
            else (0.03 if scale_known else -0.15)
        )
        x += 0.06 if len(label) >= 3 and re.search(r"[a-z]", label, re.I) else -0.2
        x += 0.06 * min(1.0, c.cls.score + 0.3)
        if not clean:
            x -= 0.3
        if not has_value:
            x -= 0.05
        if col_index in row.ambiguous_cols:
            x -= 0.25
        return x

    # ------------------------------------------------------------------ records
    def _records(self, c: _Ctx, as_of: date | None) -> list[ParsedRecord]:
        out: list[ParsedRecord] = []
        for row_index, label, fields, mapped_share, bbox in read_records(c.t, c.cls.kind, c.scale):
            x = 0.62 + 0.15 * mapped_share + 0.08 * min(1.0, c.cls.score + 0.3)
            x += (
                0.08
                if c.scale_source in ("table", "override")
                else (0.0 if c.scale_source in ("page", "document") else -0.1)
            )
            x += 0.05 if as_of and c.default_p else 0.0
            reasons = []
            if fields.get("unclean"):
                x -= 0.25
                reasons.append("unreadable number")
            if fields.get("ambiguous_cols"):
                x -= 0.2
                reasons.append("ambiguous column")
            if not label:
                x -= 0.3
                reasons.append("no row label")
            if as_of is None:
                x -= 0.2
                reasons.append("no as-of date")
            conf = _conf(x)
            out.append(
                ParsedRecord(
                    page_no=c.t.page_no,
                    table_index=c.t.index,
                    row_index=row_index,
                    record_kind=c.cls.kind,
                    label=label[:512],
                    fields=jsonable(fields),
                    as_of=as_of,
                    bbox=bbox,
                    confidence=conf,
                    quarantined=float(conf) < c.min_conf,
                    quarantine_reason=("; ".join(reasons) or f"confidence {conf} below {c.min_conf}")
                    if float(conf) < c.min_conf
                    else None,
                )
            )
        return out


class _Ctx:
    def __init__(
        self,
        t: Table,
        cls: Classification,
        scale: Scale,
        scale_source: str,
        doc: DocMeta,
        default_p: Period | None,
        default_c: float,
        min_conf: float,
    ) -> None:
        self.t, self.cls, self.scale, self.scale_source, self.doc = t, cls, scale, scale_source, doc
        self.default_p, self.default_c, self.min_conf = default_p, default_c, min_conf


def _quarantine(i: ParsedItem, min_conf: float) -> None:
    hard = i.quarantine_reason is not None and re.search(r"unreadable|no period|no row label", i.quarantine_reason)
    if float(i.confidence or 0) < min_conf or hard:
        i.quarantined = True
        if not i.quarantine_reason:
            i.quarantine_reason = f"confidence {i.confidence} below {min_conf}"
    elif i.quarantine_reason:
        i.quarantine_reason = f"kept: {i.quarantine_reason}"


def _total_check(t: Table, items: list[ParsedItem]) -> str | None:
    """Do 'Total ...' rows equal the rows above them (since the last total or sub-heading)?"""
    by_cell = {(i.row_index, i.col_index): i.value_numeric for i in items}
    passes = fails = 0
    for col in t.columns:
        run: list[Decimal] = []
        group = None
        for r in t.rows:
            if r.group != group:
                run, group = [], r.group
            v = by_cell.get((r.index, col.index))
            if r.is_total:
                if v is not None and len(run) >= 2:
                    s = sum(run, Decimal(0))
                    tol = max(abs(v) * Decimal("0.005"), Decimal(2) * (abs(v) / 10**6 if abs(v) > 10**6 else 1))
                    if abs(s - v) <= tol:
                        passes += 1
                    else:
                        fails += 1
                run = []
            elif v is not None:
                run.append(v)
    if passes == 0 and fails == 0:
        return None
    if fails == 0:
        return "pass"
    return "fail" if fails >= passes else "mixed"


# a statement that sets the scale for the whole document, wherever it is printed
_DOC_SCALE_RX = re.compile(
    r"(all|dollar)\s+(dollar\s+)?amounts[^.;\n]{0,40}\bin\s+(thousands|millions)[^.;\n]{0,60}|"
    r"\bin\s+(thousands|millions)[^.;\n]{0,40}unless\s+otherwise\s+(noted|indicated|stated)",
    re.I,
)


def _first_scale_text(page_text: str) -> str | None:
    m = re.search(
        r"[^.;\n]{0,60}\b(in|\$ in|usd in)\s+(thousands|millions|billions)[^.;\n)]{0,40}|\(\$?\s*0{3}'?s\)",
        page_text,
        re.I,
    )
    return m.group(0) if m else None


def overrides_from_rows(rows: list[dict[str, object]]) -> tuple[Override, ...]:
    out = []
    for r in rows:
        params = r.get("params") or {}
        if isinstance(params, str):
            params = json.loads(params)
        out.append(
            Override(
                override_id=int(r["override_id"]),  # type: ignore[call-overload]
                action=str(r["action"]),
                title_regex=r.get("title_regex") or None,  # type: ignore[arg-type]
                page_from=r.get("page_from"),  # type: ignore[arg-type]
                page_to=r.get("page_to"),  # type: ignore[arg-type]
                params=dict(params) if isinstance(params, dict) else {},
            )
        )
    return tuple(out)
