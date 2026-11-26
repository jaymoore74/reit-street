"""Sector-specific model builders. Each takes ``ModelInputs`` and returns a ``Book``."""

from __future__ import annotations

from collections.abc import Callable

from ..inputs import ModelInputs
from ..workbook import Book
from . import apartment

SECTORS: dict[str, Callable[[ModelInputs], Book]] = {
    "apartment": apartment.build,
}


def build_model(mi: ModelInputs) -> Book:
    try:
        builder = SECTORS[mi.sector]
    except KeyError:
        known = ", ".join(sorted(SECTORS))
        raise ValueError(f"no model builder for sector {mi.sector!r} yet (built: {known})") from None
    return builder(mi)
