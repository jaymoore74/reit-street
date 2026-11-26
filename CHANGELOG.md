# Changelog

All notable changes. Versions follow semantic versioning; the `dl_*` views and export
format are the public interface.

## [Unreleased]

## [0.2.0] - 2026-11-26

### Added
- Layered MySQL warehouse (raw, ref, stg, wh, dl, ops) managed by Alembic, with a comment
  on every table and column and a generated data dictionary.
- Content-addressed raw store with hash verification and replay.
- SEC ingestion: submissions (with paged history), filing index pages, 8-K EX-99
  exhibits, XBRL companyfacts.
- Bitemporal metric warehouse: 53 metrics, mapping rules as data, derived discrete
  quarters, restatement versions, retire-not-delete rebuilds.
- Archive import of older supplementals, IR website fetcher, document parser registry
  with a manual CSV parser.
- Jobs with locks, checkpoints, resume, dead-letter queue and run history.
- Data quality checks, monitor and alerts (log, email, webhook).
- Client export with manifest; current, as-of-date and as-recorded modes.
- CI: lint, format, type check, unit and integration tests on MySQL 8.4.
- Windows scheduled task scripts, runbooks and architecture decision records.

## [0.1.0] - 2026-11-25

### Added
- Prototype SEC EDGAR to MySQL pipeline (single schema file, no migrations). Replaced by 0.2.0.
