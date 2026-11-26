from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from reitstreet.sources.supplementals.archive import period_from_name, ticker_from_name
from reitstreet.warehouse.metrics import HASH_FIELDS, row_hash
from reitstreet.warehouse.periods import build_versions, classify_duration, fiscal_period, value_at


@pytest.mark.parametrize(
    ("days", "expected"),
    [(None, ("INSTANT", 0)), (91, ("Q", 1)), (182, ("YTD", 2)), (274, ("YTD", 3)), (366, ("FY", 4)), (30, None)],
)
def test_classify_duration(days: int | None, expected: tuple[str, int] | None) -> None:
    assert classify_duration(days) == expected


def test_fiscal_period() -> None:
    assert fiscal_period(date(2024, 3, 31), "1231") == (2024, 1)
    assert fiscal_period(date(2024, 12, 31), "1231") == (2024, 4)
    assert fiscal_period(date(2024, 9, 30), "0630") == (2025, 1)  # June year end
    assert fiscal_period(date(2025, 1, 3), "1231") == (2024, 4)  # 52/53-week calendar spill-over
    assert fiscal_period(date(2024, 3, 31), None) == (2024, 1)


def test_build_versions() -> None:
    obs = [
        {"value": 100, "as_of": date(2024, 4, 30), "tiebreak": "a"},
        {"value": 100, "as_of": date(2025, 4, 30), "tiebreak": "b"},  # repeat: not a new version
        {"value": 101, "as_of": date(2025, 5, 1), "tiebreak": "c"},  # restatement
    ]
    v = build_versions(obs)
    assert [(x["version"], x["value"], x["known_from"], x["known_to"]) for x in v] == [
        (1, 100, date(2024, 4, 30), date(2025, 5, 1)),
        (2, 101, date(2025, 5, 1), None),
    ]
    assert value_at(v, date(2025, 1, 1))["value"] == 100  # type: ignore[index]
    assert value_at(v, date(2025, 5, 1))["value"] == 101  # type: ignore[index]
    assert value_at(v, date(2024, 1, 1)) is None
    # same-day conflict: the later observation (tiebreak order) wins, no zero-length version
    same = build_versions(
        [
            {"value": 1, "as_of": date(2024, 1, 1), "tiebreak": "a"},
            {"value": 2, "as_of": date(2024, 1, 1), "tiebreak": "b"},
        ]
    )
    assert [(x["version"], x["value"]) for x in same] == [(1, 2)]


def test_row_hash_is_stable_and_sensitive() -> None:
    r = dict.fromkeys(HASH_FIELDS, None) | {"value": Decimal("1.50"), "period_end": date(2024, 3, 31)}
    h = row_hash(r)
    assert row_hash(dict(r, value=Decimal("1.500000"))) == h  # numeric normalisation
    assert row_hash(dict(r, value=Decimal("1.51"))) != h


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("PLD_Q3_2024_Supplemental.pdf", date(2024, 9, 30)),
        ("2023Q4 supplemental.pdf", date(2023, 12, 31)),
        ("pld-3q24-supp.pdf", date(2024, 9, 30)),
        ("Supp Q1-25.pdf", date(2025, 3, 31)),
        ("FY2022 annual.pdf", date(2022, 12, 31)),
        ("investor-deck.pdf", None),
    ],
)
def test_period_from_name(name: str, expected: date | None) -> None:
    assert period_from_name(name) == expected


def test_ticker_from_name() -> None:
    t = {"PLD": 1, "O": 2, "SPG": 3}
    assert ticker_from_name("PLD/2024/q3.pdf", t) == 1
    assert ticker_from_name("SPG_Q3_2024.pdf", t) == 3
    assert ticker_from_name("other_Q3_2024.pdf", t) is None  # single letters are not guessed from words
