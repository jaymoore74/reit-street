"""Title and period from the first page of a downloaded PDF, when the link said neither.

pypdf is pure Python. Any failure (encrypted, scanned, malformed) returns nothing:
metadata is a best effort and never fails a download.
"""

from __future__ import annotations

import io
import logging
import re

from .classify import infer_period
from .models import Period

log = logging.getLogger(__name__)
WS = re.compile(r"\s+")


def first_page_text(data: bytes, max_chars: int = 4000) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            return ""
        t = reader.pages[0].extract_text() if reader.pages else ""
    except Exception as e:  # pypdf raises many error types on bad files
        log.debug("pdf text extraction failed", extra={"err": f"{type(e).__name__}: {e}"})
        return ""
    return WS.sub(" ", t or "")[:max_chars]


def pdf_title_and_period(data: bytes, fye_mmdd: str | None) -> tuple[str | None, Period | None]:
    if not data.startswith(b"%PDF"):
        return None, None
    text = first_page_text(data)
    if not text:
        return None, None
    p = infer_period(text, fye_mmdd)
    if p:
        p.source = "pdf_text"
    return text[:200].strip() or None, p
