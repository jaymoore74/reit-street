"""Schema guarantees: migrations go up, down and up again; everything is documented."""

from __future__ import annotations

from pathlib import Path

import pytest

from reitstreet.core import migrate
from reitstreet.core.db import Db
from tests.integration.helpers import cli

pytestmark = pytest.mark.mysql


def test_migrations_up_down_up(db_url: str, db: Db) -> None:
    migrate.upgrade(db_url)
    assert migrate.current_revision(db_url) == migrate.head_revision()
    n_tables = db.scalar("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE()")
    migrate.downgrade(db_url, "base")
    assert db.scalar("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE()") == 1  # alembic
    migrate.upgrade(db_url)
    assert db.scalar("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE()") == n_tables


def test_every_table_and_column_has_a_comment(db_url: str, db: Db) -> None:
    migrate.upgrade(db_url)
    tables = db.query(
        """SELECT table_name AS t FROM information_schema.tables WHERE table_schema=DATABASE()
           AND table_type='BASE TABLE' AND table_name <> 'alembic_version' AND table_comment = ''"""
    )
    assert [r["t"] for r in tables] == []
    cols = db.query(
        """SELECT CONCAT(c.table_name, '.', c.column_name) AS c FROM information_schema.columns c
           JOIN information_schema.tables t ON t.table_schema=c.table_schema AND t.table_name=c.table_name
           WHERE c.table_schema=DATABASE() AND t.table_type='BASE TABLE' AND c.table_name <> 'alembic_version'
             AND c.column_comment = ''"""
    )
    assert [r["c"] for r in cols] == []


def test_layer_prefixes(db_url: str, db: Db) -> None:
    migrate.upgrade(db_url)
    names = [
        r["t"]
        for r in db.query(
            "SELECT table_name AS t FROM information_schema.tables WHERE table_schema=DATABASE() "
            "AND table_name <> 'alembic_version'"
        )
    ]
    assert all(n.split("_", 1)[0] in {"ops", "raw", "ref", "stg", "wh", "dl"} for n in names), names


def test_data_dictionary_is_current(env: Path, db: Db) -> None:
    """docs/data-dictionary.md must match the schema; regenerate with `reitstreet db-docs`."""
    assert cli("migrate", "--no-seed") == 0
    out = env / "dd.md"
    assert cli("db-docs", "--out", str(out)) == 0
    committed = Path(__file__).resolve().parents[2] / "docs" / "data-dictionary.md"
    if not committed.exists():
        pytest.skip("docs/data-dictionary.md not generated yet")
    assert out.read_text(encoding="utf-8") == committed.read_text(encoding="utf-8"), (
        "schema changed: run `reitstreet db-docs` and commit docs/data-dictionary.md"
    )
