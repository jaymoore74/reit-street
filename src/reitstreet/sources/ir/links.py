"""Anchor extraction from an IR results page.

Uses the standard library HTML parser (tolerant of broken markup, no dependency).
For each <a href> it keeps the link text and the nearest preceding text (a table row
label, a heading such as "Q3 2026"), because many IR sites label every link just
"Supplemental" and put the quarter in a row or heading next to it.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin

from .classify import classify_link, is_document_url, link_period
from .models import Link

WS = re.compile(r"\s+")
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "caption", "th", "dt", "legend", "summary", "button"}
# Markers of IR pages whose document lists are filled in by JavaScript (Q4 Inc and similar).
JS_MARKERS = re.compile(
    r"q4cdn\.com|q4inc|module_container|module-financial|data-widget|ng-app|__NEXT_DATA__|"
    r"id=\"root\"|loading\.\.\.|data-q4|q4-",
    re.I,
)


class _Anchors(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.base: str | None = None
        self.out: list[tuple[str, str, str]] = []  # href, text, context
        self._in_a: str | None = None
        self._a_text: list[str] = []
        self._recent: list[str] = []  # text since the last block boundary
        self._heading: str = ""
        self._heading_buf: list[str] | None = None
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag in ("script", "style", "noscript", "template"):
            self._skip += 1
        elif tag == "base" and a.get("href"):
            self.base = a["href"]
        elif tag == "a" and a.get("href"):
            self._in_a = a["href"] or ""
            self._a_text = [a.get("title") or a.get("aria-label") or ""]
        elif tag in HEADING_TAGS:
            self._heading_buf = []
        if tag == "tr" or tag == "li":
            self._recent = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "template"):
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._in_a is not None:
            text = WS.sub(" ", " ".join(self._a_text)).strip()
            ctx = WS.sub(" ", " ".join([self._heading, *self._recent[-6:]])).strip()
            self.out.append((self._in_a, text, ctx[-300:]))
            self._in_a = None
            self._recent.append(text)
        elif tag in HEADING_TAGS and self._heading_buf is not None:
            h = WS.sub(" ", " ".join(self._heading_buf)).strip()
            if h:
                self._heading = h[:200]
            self._heading_buf = None

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        d = data.strip()
        if not d:
            return
        if self._in_a is not None:
            self._a_text.append(d)
        else:
            self._recent.append(d)
            self._recent = self._recent[-12:]
        if self._heading_buf is not None:
            self._heading_buf.append(d)


def extract_links(html: str, page_url: str, fye_mmdd: str | None = "1231") -> list[Link]:
    """Document links on the page, de-duplicated by URL, classified, with periods."""
    p = _Anchors()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: S110  html.parser rarely raises; keep what was parsed
        pass
    base = urljoin(page_url, p.base) if p.base else page_url
    seen: dict[str, Link] = {}
    for href, text, ctx in p.out:
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url, _frag = urldefrag(urljoin(base, href.strip()))
        if not is_document_url(url):
            continue
        kind, reason = classify_link(text, url, ctx)
        lk = Link(url=url, text=text[:512], context=ctx[:512], kind=kind, kind_reason=reason)
        lk.period = link_period(text, url, ctx, fye_mmdd)
        prev = seen.get(url)
        # The same file often appears twice (icon + title); keep the better-labelled one.
        if prev is None or (prev.kind == "unknown" and kind != "unknown") or (prev.period is None and lk.period):
            seen[url] = lk
    return list(seen.values())


def looks_js_rendered(html: str) -> bool:
    return bool(JS_MARKERS.search(html))
