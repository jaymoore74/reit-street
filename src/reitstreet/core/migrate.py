"""Schema migrations (Alembic) callable from the CLI, tests and CI."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, pool

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def alembic_config(db_url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.attributes["db_url"] = db_url
    return cfg


def upgrade(db_url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(db_url), revision)


def downgrade(db_url: str, revision: str) -> None:
    command.downgrade(alembic_config(db_url), revision)


def current_revision(db_url: str) -> str | None:
    engine = create_engine(db_url, poolclass=pool.NullPool)
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def head_revision() -> str | None:
    return ScriptDirectory.from_config(alembic_config("")).get_current_head()
