# Changelog

All notable changes. Versions follow semantic versioning; the `dl_*` views and export
format are the public interface.

## [Unreleased]

### Added
- Landing page for jwalteranalytics.com (J. Walter Analytics) in `apps/landing` (static HTML, no tracking,
  Netlify config with security headers). Not deployed. Steps in
  `docs/runbooks/landing-site.md`; wording for review in `docs/landing-copy-review.md`.

### Changed
- AWS jobs send the SEC contact `Jason Moore reits@jwalteranalytics.com` by default, so
  `REITSTREET_SEC_USER_AGENT` is now an optional override and setup needs two repository
  variables, not three.

## [0.5.0] - 2026-11-26

### Added
- Client website in `apps/web` (Next.js 16, TypeScript), ADR 0013. Not deployed.
  - Sign-in for client firms: invitation and reset links from staff, scrypt passwords,
    database sessions, lockout and per-IP limits, sign out other sessions.
  - Entitlements as dated rows on plans and firms: datasets, formats, coverage (all,
    sector, ticker), research codes, history years, row caps. Enforced in SQL on every
    page and file; companies outside coverage return 404.
  - Pages: dashboard, research notes (Markdown, versioned), company pages with metric
    charts and a table view, filings, data downloads, account.
  - Downloads in CSV, Excel and Parquet from `companies`, `metrics`, `metrics_history` and
    `filings`, with filters and an as-of date. Every attempt is logged with rows, bytes and
    SHA-256; Excel and Parquet files carry the download id.
  - Newsletter lists and subscriptions (public sign-up stays pending until confirmed).
  - Email behind one setting (`RS_EMAIL_PROVIDER`: none, ses, file for development):
    newsletter confirmation links with a Confirm page, self-service "Forgot password" with
    per-IP and per-account limits. Off by default.
  - Content-Security-Policy with a per-request nonce, security headers, Origin checks.
  - Site name as a setting (`RS_SITE_NAME`); all client-facing wording listed for review in
    `docs/web-copy-review.md`.
  - Dockerfile (standalone server, non-root, RDS CA bundle), 38 unit tests, end-to-end run
    of 31 checks (38 with a development mailbox).
- Hosting as code, nothing created: CloudFormation for option B, chosen (`deploy/aws/web/web-ec2.yaml`,
  one t4g.small instance with Caddy), and option A as the upgrade path (`web-fargate.yaml`,
  Fargate behind a load balancer with ACM). Test domain `research.jwalteranalytics.com`
  (DNS at DreamHost) is the one `DomainName` parameter. Manual `ops-web-image` workflow
  builds the image on the runner and pushes it to ECR; `deploy/aws/iam/web-image-policy.json`.
  Runbook `docs/runbooks/web-hosting.md`.
- Website schema `reitstreet_app` with its own Alembic history (`migrations_app`,
  revision `a0001`), `[db] app_name` setting, `migrate --target warehouse|app|all`,
  `db-docs --target app`, generated `docs/data-dictionary-app.md`.
- `reitstreet web` admin commands: plan-create, grant, revoke, org-create, org-set,
  user-invite, user-reset, user-disable, user-enable, article, list. Runbook
  `docs/runbooks/web-admin.md`. Links use `[web] base_url`; `--email-link` sends them
  through Amazon SES when `[web] email_provider = ses` (`src/reitstreet/web/mail.py`).
- App schema revision `a0002`: index on the newsletter confirmation token.
- View `dl_metric_definitions` (revision `0007`, after 0006).
- `scripts/dev/seed_web.py`: development databases from the synthetic fixtures.
- CI: website lint, types, tests, build, end-to-end run against MySQL with a file
  mailbox, image build.
- Dev dependency PyYAML (template tests).
- Tests for the headless path with a recorded Q4 rendered-page shape (summary table plus
  yearly archive), a render failure, an HTTP 403 render and robots.txt.

### Changed
- `deploy/aws/bootstrap_db.py` and ops-bootstrap-db create the second schema and a fourth
  login, `rs_web`; `rs_loader` can write the website schema; `rs_reader` cannot see it.
- ops-migrate takes a `target` input (default `all`); ops-first-load migrates both schemas.
- `runner-setup.sh` installs Docker and a 2 GB swap file so the runner can build the
  website image.
- IR fetcher headless mode: one shared Chromium per run instead of one per page; waits for a
  document link to appear instead of network idle (Q4 sites never go idle); skips images,
  media and fonts; an HTTP 4xx/5xx render is reported as an error, not "no documents".
- `ops-ir-fetch` on the AWS runner now installs the `headless` extra and Chromium and runs
  with `--headless`, so the script-built (mostly Q4) results pages are rendered.
  `runner-setup.sh` installs Chromium's system libraries. Playwright pinned at 1.65.2.
- IR coverage: the 91 results pages that needed scripts or failed were opened in a real
  browser. 67 now profile as `works_headless`, 15 more publish no supplemental, 5 need a
  check by hand. Universe results page URLs corrected for 10 companies (HST, CPT, BNL, INN,
  JBGS, FPI, SUI and three http or fragment fixes). Migration 0006 adds `works_headless` to
  `ref_ir_profile.probe_status`.
- Link classifier: a file name that says supplemental wins over link text that only says
  presentation or release; "Quarterly Information Package" and "Investor Package" count as
  supplementals; when one file is linked twice, the supplemental label wins.

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
- Record tables, bitemporal with full lineage: `wh_property_metric` (keyed to the property
  master's staged records, property id through `wh_property_xref`), `wh_debt_instrument`,
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
- Migration 0005 (after 0004). Supplemental property rows are staged in `stg_property_record`.
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
