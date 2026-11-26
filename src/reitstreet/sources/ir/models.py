"""Types shared by the IR fetcher modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# ops_ir_check.outcome values
OK = "ok"
NOT_MODIFIED = "not_modified"
NO_DOCUMENTS = "no_documents"
JS_ONLY = "js_only"
LAYOUT_BREAK = "layout_break"
ROBOTS_BLOCKED = "robots_blocked"
HTTP_ERROR = "http_error"
NEEDS_HEADLESS = "needs_headless"
NOT_CONFIGURED = "not_configured"
NO_SOURCE = "no_source"

DOC_KINDS = ("supplemental", "earnings_release", "presentation", "exhibit_other", "unknown")


@dataclass
class Period:
    fiscal_year: int
    fiscal_quarter: int  # 1-4
    period_end: date
    source: str  # anchor, url, context, feed, pdf_text


@dataclass
class Link:
    url: str
    text: str = ""
    context: str = ""
    kind: str = "unknown"
    kind_reason: str = ""
    period: Period | None = None


@dataclass
class Discovery:
    """What one strategy saw on one page."""

    strategy: str
    outcome: str
    http_status: int | None = None
    links: list[Link] = field(default_factory=list)
    note: str = ""
    etag: str | None = None
    last_modified: str | None = None
    page_sha256: str | None = None

    @property
    def supplemental_links(self) -> list[Link]:
        return [lk for lk in self.links if lk.kind == "supplemental"]
