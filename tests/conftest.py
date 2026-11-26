"""Shared fixtures.

Unit tests need nothing. Integration tests (marker `mysql`) need a MySQL 8 server and
REITSTREET_TEST_DB_URL pointing at it, for a user allowed to create databases, e.g.
  mysql+pymysql://tester:testpw@127.0.0.1:3306/rs_test
The database named in the URL is dropped and recreated for each test that asks for it.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from reitstreet.core.db import Db, make_engine
from tests.sec_fixtures import build

TEST_URL_ENV = "REITSTREET_TEST_DB_URL"


@pytest.fixture(scope="session")
def sec_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build(tmp_path_factory.mktemp("sec"))


def _recreate(url: str) -> None:
    u = make_url(url)
    name = u.database
    assert name and name.replace("_", "").isalnum(), "test database name must be simple"
    eng = create_engine(u.set(database=""), isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text(f"DROP DATABASE IF EXISTS `{name}`"))
        c.execute(text(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"))
    eng.dispose()


@pytest.fixture
def db_url() -> str:
    url = os.environ.get(TEST_URL_ENV)
    if not url:
        pytest.skip(f"{TEST_URL_ENV} not set")
    _recreate(url)
    return url


@pytest.fixture
def env(db_url: str, tmp_path: Path, sec_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A clean working directory and settings for running the CLI against the test database."""
    monkeypatch.chdir(tmp_path)
    for k in list(os.environ):
        if k.startswith("REITSTREET_") and k != TEST_URL_ENV:
            monkeypatch.delenv(k)
    monkeypatch.setenv("REITSTREET_DB_URL", db_url)
    monkeypatch.setenv("REITSTREET_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REITSTREET_UNIVERSE_CSV", str(sec_root / "reits.csv"))
    monkeypatch.setenv("REITSTREET_SEC_USER_AGENT", "Test Runner test@reitstreet.invalid")
    monkeypatch.setenv("REITSTREET_SEC_REQUESTS_PER_SECOND", "10")
    monkeypatch.setenv("REITSTREET_LOGGING_FORMAT", "text")
    return tmp_path


@pytest.fixture
def db(db_url: str) -> Iterator[Db]:
    # READ COMMITTED: each statement sees what CLI runs committed in between (no stale snapshot)
    d = Db(make_engine(db_url, isolation_level="READ COMMITTED"))
    yield d
    d.close()
