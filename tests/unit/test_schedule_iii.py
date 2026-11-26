"""10-K Schedule III from XBRL: one row per property member, totals from the context
with no dimensions, labels from the label linkbase."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from reitstreet.core.errors import ParseError
from reitstreet.parsers import sec_schedule_iii as s3

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "schedule_iii"


@pytest.fixture(scope="module")
def sched() -> s3.ScheduleIII:
    return s3.parse_schedule_iii((FIX / "acme_2024_htm.xml").read_bytes(), (FIX / "acme_2024_lab.xml").read_bytes())


def test_totals_and_date(sched: s3.ScheduleIII) -> None:
    assert sched.as_of == date(2024, 12, 31)
    assert sched.totals["gross_total"] == Decimal("100000000")
    assert sched.totals["encumbrances"] == Decimal("12000000")
    assert sched.facts_used > 0


def test_rows(sched: s3.ScheduleIII) -> None:
    rows = {r.name: r for r in sched.property_rows}
    oak = rows["Oak Park Center"]
    assert (oak.city, oak.state) == ("Dallas", "TX")
    assert (oak.year_built, oak.year_renovated, oak.acquired_date) == (1998, 2015, date(2016, 3, 15))
    assert oak.values["gross_total"] == Decimal("26000000")
    assert not oak.is_aggregate

    other = next(r for r in sched.property_rows if r.is_aggregate)
    assert other.aggregate_count == 12 and other.values["gross_total"] == Decimal("30000000")

    river = rows["Riverside Apartments"]
    assert river.state == "GA" and river.year_built == 2005 and river.acquired_text == "2019"
    assert river.values["gross_total"] == Decimal("44000000")


def test_reconciles(sched: s3.ScheduleIII) -> None:
    rec = sched.reconciliation()
    assert rec["rows_sum"] == rec["reported_total"] == Decimal("100000000")
    assert rec["diff_pct"] == 0


def test_row_keys_are_stable(sched: s3.ScheduleIII) -> None:
    again = s3.parse_schedule_iii((FIX / "acme_2024_htm.xml").read_bytes(), (FIX / "acme_2024_lab.xml").read_bytes())
    assert [r.row_key for r in sched.rows] == [r.row_key for r in again.rows]
    assert len({r.row_key for r in sched.rows}) == len(sched.rows)


def test_without_labels_falls_back_to_member_names() -> None:
    sched = s3.parse_schedule_iii((FIX / "acme_2024_htm.xml").read_bytes(), None)
    names = {r.name for r in sched.property_rows}
    assert any(n and "Oak Park" in n for n in names)


def test_helpers() -> None:
    assert s3.split_camel("OakParkCenterMember") == "Oak Park Center"
    assert s3.field_for("RealEstateAndAccumulatedDepreciationCarryingAmountOfBuildingsAndImprovements") == "gross_bldg"
    assert s3.field_for("RealEstateAndAccumulatedDepreciationEncumbrances") == "encumbrances"
    assert s3.field_for("SomethingElse") is None
    name, loc = s3.split_label("Oak Park Center - Dallas, TX")
    assert name == "Oak Park Center" and loc.state == "TX"


def test_rejects_garbage() -> None:
    with pytest.raises(ParseError):
        s3.parse_schedule_iii(b"not xml")
    # a broken label file only costs the labels
    assert s3.parse_labels(b"<broken") == {}
