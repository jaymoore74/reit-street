# Changelog

All notable changes. Versions follow semantic versioning; the `dl_*` views and export
format are the public interface.

## [Unreleased]

## [0.4.0] - 2026-11-26

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
- Supplemental PDF parser (`parsers/pdf/`, ADR 0011): tables rebuilt from positioned words,
  stacked period headers, scale from the table, page or a document-wide statement,
  per-share and percentage units, change and share columns, parentheses as negatives.
- Table kinds and record fields as data (`table_kinds.csv`, `record_fields.csv`):
  FFO, NOI, same-store, leasing, operating, segment, debt maturity, debt detail, lease
  expiration, top tenants, development and property tables.
- Lineage on every parsed value: file hash, page, table, row, column, bounding box, raw text.
  Confidence scores; anything under 0.80 or with a hard problem is quarantined, never loaded,
  and listed in `ops_parse_quarantine`.
- Per-company parse overrides (`parse_overrides.csv`, `ref_parse_override`): skip, force kind,
  scale, confidence bar, by title pattern, page range and validity dates.
- Record tables, bitemporal with full lineage: `wh_property_metric`, `wh_debt_instrument`,
  `wh_lease_expiration`, `wh_tenant_exposure`, `wh_development_project`; `wh_debt_maturity`
  extended (Thereafter and Total buckets, debt type, share of total).
- Sector metric catalog: `ref_sector` (37 sectors), `ref_company_sector`, `ref_metric_sector`;
  55 new supplemental metrics (110 in total) with a grain of company, segment or property.
- Delivery views `dl_metric_catalog`, `dl_company_profile`, `dl_property_metrics`,
  `dl_debt_maturities`, `dl_debt_instruments`, `dl_lease_expirations`, `dl_top_tenants`,
  `dl_development_pipeline`, all exportable.
- Commands `company-profile --ticker X [--json]`, `parse-coverage`, and `parse-eval FOLDER`
  (parser quality on a folder of PDFs, no database needed).
- DQ checks: `contract.record_lineage`, `contract.record_single_current`,
  `contract.company_sectors`, `anomaly.record_pct_range`, `anomaly.schedule_shares`,
  `anomaly.property_count_swing`, `recon.debt_ladder_vs_total_debt`, `ops.parse_quarantine_rate`.

### Changed
- XML from outside sources is parsed through `core/xmlsafe.py`, which refuses DTDs.
- Mapping rules gained `col_role` (value, change, share) and `table_kind_regex`;
  quarantined items are excluded from the metric build.
- New dependency: pdfplumber 0.11.11 (MIT) with pdfminer.six, pypdfium2, Pillow pinned in
  `constraints.txt`.
- Migration 0005.

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
