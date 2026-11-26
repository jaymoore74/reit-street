# Changelog

All notable changes. Versions follow semantic versioning; the `dl_*` views and export
format are the public interface.

## [Unreleased]

### Added
- Property master (ADR 0010): one stable id per real property across REITs and years.
  `stg_property_record` keeps every row each source gives, with lineage;
  `wh_property_xref` links rows to properties with the rule, score and review status;
  `wh_property_alias` keeps names, addresses and source references;
  `wh_property_ownership` holds ownership spells (reported or inferred acquired and disposed
  dates, knowledge time); `dl_properties` is the client view. Migration 0004 replaces the
  never-filled baseline `wh_property`.
- 10-K Schedule III loader from XBRL (property and aggregate rows, totals for
  reconciliation); list loaders for HTML tables, CSV, JSON and XLSX; `import-properties`
  for files; US Census geocoding with a cache.
- Source map for 150 equity REITs (`property_sources.csv`, `ref_property_source`) and
  property types (`property_types.csv`, 36 types with sector and size unit).
- Commands `load-properties`, `import-properties`, `build-properties`,
  `geocode-properties`, `property-coverage`, `property-review`; seven data quality checks.
- `docs/property-coverage.md`, runbook `docs/runbooks/properties.md`.

### Changed
- XML from outside sources is parsed through `core/xmlsafe.py`, which refuses DTDs.

## [0.3.0] - 2026-11-26

### Added
- Production on AWS: GitHub Actions workflows for the existing Aurora MySQL cluster
  (`ops-bootstrap-db`, `ops-migrate`, `ops-first-load`, `ops-sync`, `ops-weekly`,
  `ops-monitor`) on a self-hosted runner in the cluster's VPC. Logins from AWS Secrets
  Manager, raw store, logs and exports copied to S3, failures raised as GitHub issues.
  Runbook `docs/runbooks/cloud-setup.md`, ADR 0008.
- `[db] ssl_ca` setting: TLS with certificate and host name verification.
- `universe/reits.csv` is now versioned in the repo; scheduled runs read it from there.
- IR website fetcher: strategy chain (Q4 feed with a key, static HTML, headless Chromium
  as last resort) per results page, with SEC 8-K exhibits as fallback and cross-check.
- `ref_ir_profile` loaded from a versioned `ir_profiles.csv` (189 companies, from a live
  probe of 186 sites), `ref_web_host` (robots.txt cache), `stg_ir_link` (every link found,
  with kind, period and status), `ops_ir_check` (one row per page check) and the
  `ops_ir_coverage` view.
- Link classification and fiscal period inference from link text, file name, headings and
  PDF text; magic-byte check on every download.
- Polite crawling: robots.txt (RFC 9309), per-host rate limits with Crawl-delay,
  conditional GETs, generic User-Agent with no personal details.
- Overdue supplemental check (company's own lag, earnings release signal), layout-break and
  failing-page alerts, `dq` checks `freshness.supplemental_overdue` and `ops.ir_site_health`.
- `ir-coverage` command, `ir_fetch.bat` scheduled at 06:00 and 15:00 on weekdays, runbook,
  ADR 0009, coverage baseline.

### Changed
- Non-SEC requests no longer fall back to the SEC User-Agent.
- `fetch-ir` gained `--kinds`, `--backfill`, `--strategy`, `--headless`, `--force`,
  `--no-download`; downloads are capped per company per run.

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
