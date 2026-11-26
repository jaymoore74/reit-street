# ADR 0007: MySQL 8 as the warehouse

Status: accepted, 2026-11-26

## Context
The product needs a relational store that clients and analysts can query, that runs on
a Windows workstation today and a managed service later.

## Decision
MySQL 8.4 LTS (8.0 compatible), InnoDB, utf8mb4. Generated columns, JSON, window
functions and `GET_LOCK` are used. Tests run against a real MySQL, never SQLite.

## Consequences
- Moves to a managed MySQL (RDS, Cloud SQL, Azure) without code changes.
- No native temporal tables, so bitemporality is implemented in the schema (ADR 0004).
- Analytical scale is fine into the hundreds of millions of rows; if that changes, the
  `dl_*` layer can be replicated to a columnar store without changing ingestion.
