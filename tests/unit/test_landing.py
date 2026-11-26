"""Checks on the static landing page in apps/landing."""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "apps" / "landing"
LONG_DASHES = ("–", "—")


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_pages_exist() -> None:
    for name in ("index.html", "404.html", "favicon.svg", "robots.txt", "netlify.toml"):
        assert (ROOT / name).is_file(), name


def test_no_long_dashes() -> None:
    for path in ROOT.iterdir():
        text = path.read_text(encoding="utf-8")
        assert not any(d in text for d in LONG_DASHES), path.name


def test_no_external_scripts_or_trackers() -> None:
    html = _read("index.html")
    assert not re.search(r"<script[^>]+src=", html)
    for word in ("googletagmanager", "google-analytics", "gtag(", "facebook.net"):
        assert word not in html


def test_contact_and_links() -> None:
    html = _read("index.html")
    assert 'href="mailto:jason@jwalteranalytics.com"' in html
    assert 'href="https://www.linkedin.com/in/jasonmoorecfa"' in html
    assert "utm_" not in html


def test_csp_hash_matches_inline_script() -> None:
    html = _read("index.html")
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert len(scripts) == 1
    digest = base64.b64encode(hashlib.sha256(scripts[0].encode()).digest()).decode()
    assert f"'sha256-{digest}'" in _read("netlify.toml")
