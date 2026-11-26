"""NAV and AFFO models: sourced inputs, explicit assumptions, live-formula Excel output.

See docs/adr/0012-nav-affo-models.md.
"""

from __future__ import annotations

from pathlib import Path

from .excel import write_xlsx
from .inputs import ModelInputs, load_inputs
from .sectors import SECTORS, build_model
from .workbook import Book

INPUTS_DIR = Path(__file__).parent / "inputs"

__all__ = ["INPUTS_DIR", "SECTORS", "Book", "ModelInputs", "build_model", "load_inputs", "write_xlsx"]
