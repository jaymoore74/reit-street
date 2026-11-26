"""Supplemental PDF parser on synthetic pages: periods, scale, units, records, quarantine, overrides."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from reitstreet.core.errors import ParseError
from reitstreet.parsers.documents import DocMeta, Override
from reitstreet.parsers.pdf.evaluate import core_metrics_by_sector, evaluate_folder, load_rules, match_metrics
from reitstreet.parsers.pdf.parser import SupplementalPdfParser
from tests.pdf import samples

Q3 = date(2026, 9, 30)


def meta(overrides: tuple[Override, ...] = ()) -> DocMeta:
    return DocMeta(1, 1, "supplemental", "test", "application/pdf", "X_2026Q3.pdf", Q3, 12, overrides)


def items_by(res, label_start: str):  # type: ignore[no-untyped-def]
    return [i for i in res.items if (i.metric_label or i.row_label).startswith(label_start)]


def test_ffo_periods_scale_and_per_share_units() -> None:
    res = SupplementalPdfParser().parse(samples.ffo_and_debt(), meta())
    ffo = {t.table_kind for t in res.tables}
    assert {"ffo", "debt_maturity"} <= ffo
    t = next(t for t in res.tables if t.table_kind == "ffo")
    assert t.scale_factor == Decimal("1000") and t.scale_source == "table"

    ffo_q = [i for i in items_by(res, "FFO attributable") if i.period_type == "Q" and i.period_end == Q3]
    assert len(ffo_q) == 1 and ffo_q[0].value_numeric == Decimal("222778000")
    assert ffo_q[0].unit_hint == "USD" and not ffo_q[0].quarantined
    ytd = [i for i in items_by(res, "FFO attributable") if i.period_type == "YTD" and i.period_end == Q3]
    assert ytd[0].value_numeric == Decimal("645411000")
    prior = [i for i in items_by(res, "FFO attributable") if i.period_end == date(2025, 9, 30) and i.period_type == "Q"]
    assert prior[0].value_numeric == Decimal("213320000")

    per_share = items_by(res, "FFO per diluted share")
    assert {i.unit_hint for i in per_share} == {"USD/shares"}
    assert next(i for i in per_share if i.period_type == "Q" and i.period_end == Q3).value_numeric == Decimal("1.21")

    gain = next(i for i in items_by(res, "Gain on sale") if i.period_type == "Q" and i.period_end == Q3)
    assert gain.value_numeric == Decimal("-12004000")  # parentheses are negative


def test_lineage_fields_present() -> None:
    res = SupplementalPdfParser().parse(samples.ffo_and_debt(), meta())
    for i in res.items:
        assert i.page_no == 1 and i.table_index is not None and i.row_index is not None
        assert i.bbox and len(i.bbox) == 4 and i.raw_text
        assert i.confidence is not None
    for r in res.records:
        assert r.bbox and r.confidence is not None and r.as_of == Q3


def test_debt_maturity_records() -> None:
    res = SupplementalPdfParser().parse(samples.ffo_and_debt(), meta())
    recs = {r.label: r for r in res.records if r.record_kind == "debt_maturity"}
    assert set(recs) == {"2027", "2028", "Thereafter", "Total"}
    assert recs["2028"].fields["amounts"] == {"Secured": "12000000", "Unsecured": "300000000", "Total": "312000000"}
    assert recs["2028"].fields["wtd_avg_rate"] == "3.9"
    assert recs["Total"].fields["is_total"] is True and recs["2027"].fields["is_total"] is False
    assert not any(r.quarantined for r in recs.values())


def test_tenants_and_lease_expirations() -> None:
    res = SupplementalPdfParser().parse(samples.net_lease_tenants(), meta())
    tenants = [r for r in res.records if r.record_kind == "tenant"]
    assert [r.label for r in tenants][:2] == ["7-Eleven", "Mister Car Wash"]
    assert tenants[0].fields["n_properties"] == "148"
    assert tenants[0].fields["abr"] == "48210000"  # table says dollars in thousands
    assert tenants[0].fields["abr_pct"] == "5.6"
    exp = {r.label: r for r in res.records if r.record_kind == "lease_expiration"}
    assert exp["2027"].fields["n_leases"] == "88" and exp["Total"].fields["is_total"] is True


def test_merged_headers_are_not_guessed() -> None:
    res = SupplementalPdfParser().parse(samples.net_lease_tenants(merged_headers=True), meta())
    tenants = [r for r in res.records if r.record_kind == "tenant"]
    assert tenants and all(r.quarantined for r in tenants)
    assert all("ambiguous column" in (r.quarantine_reason or "") for r in tenants)
    assert all("abr" not in r.fields and "n_properties" not in r.fields for r in tenants)


def test_same_store_change_column_and_property_records() -> None:
    res = SupplementalPdfParser().parse(samples.apartment_same_store(), meta())
    noi = items_by(res, "Same-store NOI")
    change = [i for i in noi if i.col_role == "change"]
    assert len(change) == 1 and change[0].value_numeric == Decimal("3.4") and change[0].unit_hint == "pct"
    assert change[0].period_end == Q3  # a change column takes the latest period to its left
    occ = next(i for i in items_by(res, "Average physical occupancy") if i.col_role == "value" and i.period_end == Q3)
    assert occ.value_numeric == Decimal("95.8") and occ.unit_hint == "pct"
    props = {r.label: r.fields for r in res.records if r.record_kind == "property"}
    assert props["The Grove"] == {
        "city": "Austin",
        "state": "TX",
        "units": "412",
        "occupancy": "96.1",
        "is_total": False,
    }


def test_unknown_scale_is_quarantined_not_loaded_at_face_value() -> None:
    res = SupplementalPdfParser().parse(samples.unreadable_scale(), meta())
    money = [i for i in res.items if i.unit_hint == "USD"]
    assert money and all(i.quarantined and "scale unknown" in (i.quarantine_reason or "") for i in money)


def test_document_wide_scale_statement_applies() -> None:
    from tests.pdf.pdfgen import PdfDoc

    d = PdfDoc()
    p = d.page()
    p.text(36, 40, "Notes", size=12, bold=True)
    p.text(36, 60, "All dollar amounts are in thousands, except per share data.", size=8)
    q = d.page()
    q.text(36, 40, "Net Operating Income", size=14, bold=True)
    xs = [36, 330, 410]
    q.text(300, 64, "Three Months Ended September 30,", size=8, bold=True)
    q.row(76, ["", "2026", "2025"], xs, bold=True)
    q.row(92, ["Rental revenue", "512,000", "498,000"], xs)
    q.row(106, ["Net operating income", "350,100", "340,200"], xs)
    res = SupplementalPdfParser().parse(d.render(), meta())
    t = next(t for t in res.tables if t.table_kind == "noi")
    assert t.scale_source == "document"
    noi = next(i for i in items_by(res, "Net operating income") if i.period_end == Q3)
    assert noi.value_numeric == Decimal("350100000") and not noi.quarantined


def test_overrides_skip_scale_and_force_kind() -> None:
    skip = Override(1, "skip", title_regex="debt maturit")
    res = SupplementalPdfParser().parse(samples.ffo_and_debt(), meta((skip,)))
    assert not [r for r in res.records if r.record_kind == "debt_maturity"]
    assert items_by(res, "FFO attributable")

    millions = Override(2, "scale", title_regex="funds from operations", params={"money": 1000000})
    res = SupplementalPdfParser().parse(samples.ffo_and_debt(), meta((millions,)))
    ffo = next(i for i in items_by(res, "FFO attributable") if i.period_type == "Q" and i.period_end == Q3)
    assert ffo.value_numeric == Decimal("222778000000")
    assert next(t for t in res.tables if t.table_kind == "ffo").scale_source == "override"

    force = Override(3, "force_kind", title_regex="net operating income", params={"kind": "same_store"})
    res = SupplementalPdfParser().parse(samples.unreadable_scale(), meta((force,)))
    assert res.tables[0].table_kind == "same_store"


def test_min_confidence_override_and_config_hash() -> None:
    p = SupplementalPdfParser()
    strict = Override(4, "min_confidence", params={"value": 0.99})
    res = p.parse(samples.ffo_and_debt(), meta((strict,)))
    assert res.items and all(i.quarantined for i in res.items)
    assert p.config_hash(meta()) != p.config_hash(meta((strict,)))


def test_not_a_pdf() -> None:
    with pytest.raises(ParseError):
        SupplementalPdfParser().parse(b"<html>nope</html>", meta())


def test_seed_rules_match_parsed_labels() -> None:
    res = SupplementalPdfParser().parse(samples.apartment_same_store(), meta())
    kinds = {(t.page_no, t.table_index): t.table_kind for t in res.tables}
    found = match_metrics(res.items, kinds, load_rules())
    assert {"SS_NOI_GROWTH", "SS_REVENUE_GROWTH", "SS_EXPENSE_GROWTH"} <= found
    res = SupplementalPdfParser().parse(samples.ffo_and_debt(), meta())
    kinds = {(t.page_no, t.table_index): t.table_kind for t in res.tables}
    assert {"FFO_NAREIT", "FFO_PER_SHARE"} <= match_metrics(res.items, kinds, load_rules())


def test_core_catalog_covers_every_universe_label() -> None:
    core = core_metrics_by_sector()
    for label in (
        "residential - apartments",
        "net lease",
        "office",
        "industrial",
        "healthcare",
        "mortgage - commercial",
    ):
        assert len(core[label]) >= 5, label
    assert "AFFO_PER_SHARE" in core["net lease"] or "AFFO" in core["net lease"]
    assert "BOOK_VALUE_PER_SHARE" in core["mortgage - commercial"]


def test_parse_eval_folder(tmp_path: Path) -> None:
    (tmp_path / "AVB_2026Q3_supplemental.pdf").write_bytes(samples.apartment_same_store())
    (tmp_path / "O_2026Q3_supplemental.pdf").write_bytes(samples.net_lease_tenants())
    (tmp_path / "BAD_2026Q3.pdf").write_bytes(b"not a pdf")
    uni = tmp_path / "reits.csv"
    uni.write_text("ticker,sector\nAVB,Residential - Apartments\nO,Net Lease\n", encoding="utf-8")
    results, summary = evaluate_folder(tmp_path, uni)
    by = {r.file: r for r in results}
    assert by["BAD_2026Q3.pdf"].status == "failed"
    avb = by["AVB_2026Q3_supplemental.pdf"]
    assert avb.ticker == "AVB" and avb.period_end == "2026-09-30" and avb.records == 3
    assert "SS_NOI_GROWTH" in avb.metrics_found
    assert summary["files"] == 3 and summary["files_failed"] == 1
    assert summary["record_kinds"]["tenant"] == 5
