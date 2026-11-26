# ADR 0002: Alembic migrations with raw SQL, no ORM

Status: accepted, 2026-11-26

## Context
The schema will change for years and runs on machines we do not control (a Windows PC
first, then servers). Changes must be reviewable, reversible and identical everywhere.

## Decision
Alembic owns the schema. Revisions use `op.execute()` with explicit MySQL DDL. Application
code uses SQLAlchemy Core `text()` with named parameters; no ORM models.

## Consequences
- The SQL a reviewer reads is the SQL that runs; MySQL features (generated columns,
  partial uniqueness via `is_live`, JSON, comments) are used directly.
- No model/schema drift, because there are no models. Autogenerate is not available.
- CI runs upgrade, downgrade and upgrade on MySQL 8.4 for every change.
