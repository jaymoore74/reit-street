"""Alembic environment for the client web application schema (default `reitstreet_app`).

The web schema is separate from the warehouse schema (ADR 0013): it holds client accounts,
password hashes, sessions and audit logs, so analysts' read-only login (`rs_reader`) has no
access to it, and it can get its own backup and retention policy. The URL is the warehouse
URL with the database name replaced by `[db] app_name`, never taken from alembic.ini."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from reitstreet.core import config as cfgmod

alembic_cfg = context.config


def _url() -> str:
    url = alembic_cfg.attributes.get("db_url") or alembic_cfg.get_main_option("sqlalchemy.url")
    return url or cfgmod.load().app_sqlalchemy_url()


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
