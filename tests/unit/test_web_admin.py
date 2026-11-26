"""Pure parts of the web admin tools and the AWS database bootstrap (no database needed)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from reitstreet.core.errors import ConfigError
from reitstreet.web import admin

ROOT = Path(__file__).resolve().parents[2]


def _bootstrap():  # deploy/ is not a package; load the script as a module
    spec = importlib.util.spec_from_file_location("bootstrap_db", ROOT / "deploy" / "aws" / "bootstrap_db.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bootstrap_grants_keep_logins_apart() -> None:
    b = _bootstrap()
    schemas = {"wh": "reitstreet", "app": "reitstreet_app"}
    web = b.grant_statements("web", schemas)
    assert web == [
        "GRANT SELECT, SHOW VIEW ON `reitstreet`.* TO 'rs_web'@'%'",
        "GRANT SELECT, INSERT, UPDATE ON `reitstreet_app`.* TO 'rs_web'@'%'",
    ]
    # analysts never see client accounts; the web login can never delete
    assert all("reitstreet_app" not in g for g in b.grant_statements("reader", schemas))
    assert not any("DELETE" in g or "ALL" in g for g in web)
    assert any("reitstreet_app" in g and "ALL PRIVILEGES" in g for g in b.grant_statements("migrator", schemas))


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        ("dataset", "metrics", "metrics"),
        ("format", "parquet", "parquet"),
        ("coverage_ticker", " pld ", "PLD"),
        ("coverage_all", "", ""),
        ("history_years", "5", "5"),
        ("max_rows", "250000", "250000"),
    ],
)
def test_validate_grant_accepts(kind: str, value: str, expected: str) -> None:
    assert admin.validate_grant(kind, value) == expected


@pytest.mark.parametrize(
    ("kind", "value"),
    [("dataset", "all"), ("format", "pdf"), ("history_years", "0"), ("max_rows", "x"), ("nonsense", "1")],
)
def test_validate_grant_refuses(kind: str, value: str) -> None:
    with pytest.raises(ConfigError):
        admin.validate_grant(kind, value)


def test_parse_article() -> None:
    meta, body = admin.parse_article("---\nslug: a-note\ntitle: A note\n---\nHello\n")
    assert meta == {"slug": "a-note", "title": "A note", "status": "draft"} and body.strip() == "Hello"
    with pytest.raises(ConfigError):
        admin.parse_article("no front matter")


def test_tokens_are_stored_only_as_hashes() -> None:
    t = admin.new_token()
    assert len(t) >= 40 and admin.token_hash(t) != t and len(admin.token_hash(t)) == 64
