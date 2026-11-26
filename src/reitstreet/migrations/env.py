"""Alembic environment. The database URL comes from reitstreet settings
(config.ini or REITSTREET_DB_* environment variables), never from alembic.ini."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from reitstreet.core import config as cfgmod

alembic_cfg = context.config


def _url() -> str:
    url = alembic_cfg.attributes.get("db_url") or alembic_cfg.get_main_option("sqlalchemy.url")
    return url or cfgmod.load().sqlalchemy_url()


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
