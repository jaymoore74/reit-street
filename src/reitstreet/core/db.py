"""Thin database layer over SQLAlchemy Core.

All SQL is written by hand with bound parameters (:name). We deliberately do not use
an ORM: the warehouse is MySQL-specific (generated columns, REGEXP_LIKE, GET_LOCK),
and explicit SQL is easier to review, explain and tune over the years.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

Row = dict[str, Any]


def make_engine(url: str, **kw: Any) -> Engine:
    return create_engine(url, pool_pre_ping=True, pool_recycle=3600, future=True, **kw)


class Db:
    """One connection, explicit transactions. Not thread safe: one Db per worker."""

    def __init__(self, engine: Engine):
        self.engine = engine
        self.conn: Connection = engine.connect()
        self.conn.exec_driver_sql("SET SESSION time_zone = '+00:00'")
        self.conn.commit()

    @classmethod
    def from_url(cls, url: str) -> Db:
        return cls(make_engine(url))

    # ---------------------------------------------------------------- reads
    def query(self, sql: str, params: Mapping[str, Any] | None = None) -> list[Row]:
        res = self.conn.execute(text(sql), dict(params or {}))
        return [dict(r._mapping) for r in res]

    def iter(self, sql: str, params: Mapping[str, Any] | None = None) -> Iterator[Row]:
        res = self.conn.execution_options(stream_results=True).execute(text(sql), dict(params or {}))
        for r in res:
            yield dict(r._mapping)

    def one(self, sql: str, params: Mapping[str, Any] | None = None) -> Row | None:
        r = self.conn.execute(text(sql), dict(params or {})).first()
        return dict(r._mapping) if r is not None else None

    def scalar(self, sql: str, params: Mapping[str, Any] | None = None) -> Any:
        return self.conn.execute(text(sql), dict(params or {})).scalar()

    # ---------------------------------------------------------------- writes
    def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> int:
        """Run one statement. Returns lastrowid for inserts that created a row, else rowcount."""
        res = self.conn.execute(text(sql), dict(params or {}))
        lid = getattr(res, "lastrowid", None)
        if sql.lstrip().upper().startswith("INSERT") and lid:
            return int(lid)
        return int(res.rowcount or 0)

    def executemany(self, sql: str, rows: Iterable[Mapping[str, Any]], chunk: int = 2000) -> int:
        """Bulk statement. Returns rows affected (for INSERT IGNORE: rows actually inserted)."""
        n = 0
        batch: list[dict[str, Any]] = []
        for r in rows:
            batch.append(dict(r))
            if len(batch) >= chunk:
                n += int(self.conn.execute(text(sql), batch).rowcount or 0)
                batch = []
        if batch:
            n += int(self.conn.execute(text(sql), batch).rowcount or 0)
        return n

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    @contextmanager
    def transaction(self) -> Iterator[Db]:
        """Commit on success, roll back on error. Use around each unit of work."""
        try:
            yield self
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def close(self) -> None:
        try:
            self.conn.close()
        finally:
            self.engine.dispose()


def in_clause(name: str, values: Sequence[Any]) -> tuple[str, dict[str, Any]]:
    """Build 'IN (:name_0, :name_1, ...)' with params, for lists of bound values."""
    keys = [f"{name}_{i}" for i in range(len(values))]
    return "(" + ",".join(f":{k}" for k in keys) + ")", dict(zip(keys, values))
