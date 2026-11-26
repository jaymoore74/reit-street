"""Baseline schema: raw, reference, staging, warehouse and ops layers.

Layers (single MySQL schema, table name prefix = layer):
  raw_  immutable capture of every byte received (content-addressed objects + fetch log)
  ref_  reference/master data: companies, IR sources, metric dictionary and mappings
  stg_  parsed source records, faithful to the source, each row traceable to a raw object
  wh_   curated warehouse: conformed, bitemporal, lineage on every value
  ops_  operations: job runs, checkpoints, dead-letter queue, data quality, alerts

Every table and column carries a COMMENT; docs/data-dictionary.md is generated from them
and tests/integration/test_schema_contract.py fails CI if one is missing.

Revision ID: 0001
Revises:
Create Date: 2026-11-26
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

T = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"

TABLES: list[tuple[str, str]] = [
    # ====================================================================== ops
    (
        "ops_job_run",
        f"""
CREATE TABLE ops_job_run (
  run_id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key of one execution of one job',
  job               VARCHAR(64) NOT NULL COMMENT 'Job name, e.g. sec-sync, build-metrics, dq, import-archive',
  args              JSON NULL COMMENT 'Command line arguments the run was started with',
  status            ENUM('running','succeeded','partial','failed','aborted') NOT NULL DEFAULT 'running' COMMENT 'succeeded: all items ok; partial: some items failed; failed: run-level failure; aborted: died without finishing (set by monitor)',
  host              VARCHAR(128) NULL COMMENT 'Machine that ran the job',
  pid               INT UNSIGNED NULL COMMENT 'Operating system process id',
  code_version      VARCHAR(64) NULL COMMENT 'Package version and git commit of the code that ran',
  resumed_from_run_id BIGINT UNSIGNED NULL COMMENT 'Run whose completed items this run skipped (--resume)',
  started_at        DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'UTC start time',
  heartbeat_at      DATETIME(6) NULL COMMENT 'UTC time of the last progress update; a stale heartbeat means the process died',
  finished_at       DATETIME(6) NULL COMMENT 'UTC end time',
  items_total       INT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'Work items attempted (usually companies or documents)',
  items_ok          INT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'Items that finished without error',
  items_failed      INT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'Items that raised an error (see ops_job_item, ops_dead_letter)',
  items_skipped     INT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'Items skipped (unchanged source or already done in the resumed run)',
  http_requests     INT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'HTTP requests made',
  rows_written      BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'Rows inserted or changed across all tables',
  error_summary     TEXT NULL COMMENT 'First lines of the errors, for a quick look',
  PRIMARY KEY (run_id),
  KEY ix_job_started (job, started_at),
  KEY ix_status (status)
) {T} COMMENT='One row per job execution. The monitor alerts on failed, partial, aborted and overdue runs.'""",
    ),
    (
        "ops_job_item",
        f"""
CREATE TABLE ops_job_item (
  run_id        BIGINT UNSIGNED NOT NULL COMMENT 'Run this item belongs to',
  item_key      VARCHAR(191) NOT NULL COMMENT 'Stable key of the work item, e.g. cik:1045609 or doc:123',
  status        ENUM('running','ok','failed','skipped') NOT NULL COMMENT 'Outcome of the item',
  started_at    DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'UTC start',
  finished_at   DATETIME(6) NULL COMMENT 'UTC end',
  rows_written  INT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'Rows written while processing this item',
  detail        VARCHAR(1024) NULL COMMENT 'Short outcome note or error message',
  PRIMARY KEY (run_id, item_key),
  CONSTRAINT fk_item_run FOREIGN KEY (run_id) REFERENCES ops_job_run (run_id)
) {T} COMMENT='Per-item checkpoints. --resume <run_id> skips items that finished ok in that run.'""",
    ),
    (
        "ops_dead_letter",
        f"""
CREATE TABLE ops_dead_letter (
  dl_id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  stage           VARCHAR(64) NOT NULL COMMENT 'Pipeline stage that failed, e.g. sec.companyfacts.parse, supplemental.parse',
  item_key        VARCHAR(191) NOT NULL COMMENT 'Work item that failed (same key as ops_job_item)',
  sha256          CHAR(64) NULL COMMENT 'Raw object being processed, when there is one (re-parse it with replay)',
  sha_key         CHAR(64) AS (COALESCE(sha256, '')) STORED COMMENT 'Non-null copy of sha256 for the uniqueness rule',
  company_id      INT UNSIGNED NULL COMMENT 'Company concerned, when known',
  doc_id          BIGINT UNSIGNED NULL COMMENT 'Document concerned, when known',
  error_class     VARCHAR(128) NOT NULL COMMENT 'Python exception class',
  error_message   TEXT NOT NULL COMMENT 'Exception message',
  traceback       MEDIUMTEXT NULL COMMENT 'Full traceback for the engineer fixing it',
  status          ENUM('open','resolved','ignored') NOT NULL DEFAULT 'open' COMMENT 'open: needs attention; resolved: fixed and reprocessed; ignored: accepted as unfixable',
  is_open         TINYINT AS (IF(status = 'open', 1, NULL)) STORED COMMENT '1 while open, NULL otherwise (lets resolved history repeat)',
  occurrences     INT UNSIGNED NOT NULL DEFAULT 1 COMMENT 'Times this same failure was hit while open',
  first_run_id    BIGINT UNSIGNED NULL COMMENT 'Run that first hit it',
  last_run_id     BIGINT UNSIGNED NULL COMMENT 'Most recent run that hit it',
  first_seen_at   DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'UTC first failure',
  last_seen_at    DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'UTC latest failure',
  resolved_at     DATETIME(6) NULL COMMENT 'UTC time it was resolved or ignored',
  resolution_note VARCHAR(1024) NULL COMMENT 'What was done about it',
  PRIMARY KEY (dl_id),
  UNIQUE KEY uq_open (stage, item_key, sha_key, is_open),
  KEY ix_status (status, stage)
) {T} COMMENT='Dead-letter / QA queue: anything that could not be processed. Nothing is silently dropped.'""",
    ),
    (
        "ops_dq_result",
        f"""
CREATE TABLE ops_dq_result (
  result_id   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  run_id      BIGINT UNSIGNED NULL COMMENT 'dq run that produced it',
  check_code  VARCHAR(64) NOT NULL COMMENT 'Check identifier, documented in reitstreet.quality.checks',
  severity    ENUM('info','warn','error') NOT NULL COMMENT 'How bad a failure of this check is',
  status      ENUM('pass','fail','error') NOT NULL COMMENT 'pass, fail (data problem) or error (the check itself crashed)',
  company_id  INT UNSIGNED NULL COMMENT 'Company the result is about, if any',
  subject     VARCHAR(255) NULL COMMENT 'What was checked, e.g. TOTAL_ASSETS 2024-12-31',
  observed    VARCHAR(255) NULL COMMENT 'Observed value',
  expected    VARCHAR(255) NULL COMMENT 'Expected value or tolerance',
  details     JSON NULL COMMENT 'Extra context (ids, values) for investigation',
  checked_at  DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'UTC time of the check',
  PRIMARY KEY (result_id),
  KEY ix_check (check_code, checked_at),
  KEY ix_run (run_id),
  KEY ix_company (company_id, checked_at)
) {T} COMMENT='Data quality results. One summary pass row per check per run plus one row per failure.'""",
    ),
    (
        "ops_alert",
        f"""
CREATE TABLE ops_alert (
  alert_id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  created_at      DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'UTC time raised',
  severity        ENUM('info','warn','error','critical') NOT NULL COMMENT 'Alert severity',
  source          VARCHAR(64) NOT NULL COMMENT 'Who raised it: job name, monitor, dq',
  title           VARCHAR(255) NOT NULL COMMENT 'One-line summary',
  body            TEXT NULL COMMENT 'Details',
  dedupe_key      VARCHAR(191) NOT NULL COMMENT 'Same key within the suppression window is not re-sent',
  channels        VARCHAR(128) NULL COMMENT 'Channels it was delivered to (log, email, webhook)',
  suppressed      TINYINT(1) NOT NULL DEFAULT 0 COMMENT '1 if not sent because an identical alert went out recently',
  delivery_error  TEXT NULL COMMENT 'Error from a channel that failed to deliver',
  PRIMARY KEY (alert_id),
  KEY ix_dedupe (dedupe_key, created_at)
) {T} COMMENT='Every alert raised, sent or suppressed.'""",
    ),
    # ====================================================================== raw
    (
        "raw_object",
        f"""
CREATE TABLE raw_object (
  sha256        CHAR(64) NOT NULL COMMENT 'SHA-256 of the exact bytes received; also the storage address',
  bytes         BIGINT UNSIGNED NOT NULL COMMENT 'Uncompressed size',
  content_type  VARCHAR(128) NULL COMMENT 'Content-Type reported by the source',
  storage_path  VARCHAR(255) NOT NULL COMMENT 'Path relative to the raw store root (sha256/ab/cd/<sha>.gz)',
  first_seen_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'UTC time the content was first stored',
  PRIMARY KEY (sha256)
) {T} COMMENT='Immutable content-addressed objects. Never updated or deleted by the pipeline.'""",
    ),
    (
        "raw_fetch",
        f"""
CREATE TABLE raw_fetch (
  fetch_id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key of one retrieval',
  run_id            BIGINT UNSIGNED NULL COMMENT 'Job run that made the request',
  source            VARCHAR(32) NOT NULL COMMENT 'Source family: sec, ir_website, email, manual_archive',
  url               VARCHAR(2048) NOT NULL COMMENT 'Requested URL (file:// URI for local imports)',
  url_hash          CHAR(64) AS (SHA2(url, 256)) STORED COMMENT 'Hash of url, for indexing',
  http_status       SMALLINT UNSIGNED NULL COMMENT 'HTTP status (NULL for local files and network errors)',
  sha256            CHAR(64) NULL COMMENT 'Content received; NULL when the request failed',
  response_headers  JSON NULL COMMENT 'Selected response headers (content-type, last-modified, etag, date)',
  elapsed_ms        INT UNSIGNED NULL COMMENT 'Request duration',
  error             TEXT NULL COMMENT 'Error, when the request failed',
  fetched_at        DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'UTC time of the request',
  PRIMARY KEY (fetch_id),
  KEY ix_url (url_hash, fetch_id),
  KEY ix_sha (sha256),
  KEY ix_run (run_id),
  CONSTRAINT fk_fetch_object FOREIGN KEY (sha256) REFERENCES raw_object (sha256)
) {T} COMMENT='Every retrieval attempt, successful or not. Replay reads from here.'""",
    ),
    # ====================================================================== ref
    (
        "ref_company",
        f"""
CREATE TABLE ref_company (
  company_id            INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key used everywhere',
  cik                   INT UNSIGNED NOT NULL COMMENT 'SEC Central Index Key (permanent company id)',
  ticker                VARCHAR(16) NULL COMMENT 'Current primary ticker',
  name                  VARCHAR(255) NOT NULL COMMENT 'Current SEC conformed name',
  exchange              VARCHAR(32) NULL COMMENT 'Primary listing exchange',
  sector                VARCHAR(64) NULL COMMENT 'Property sector (from the universe file)',
  reit_type             VARCHAR(32) NULL COMMENT 'Equity, mortgage or hybrid',
  listing_note          VARCHAR(255) NULL COMMENT 'Listing remarks from the universe file',
  ir_platform           VARCHAR(64) NULL COMMENT 'IR website vendor (e.g. Q4, Notified), drives the future IR fetcher',
  sic                   SMALLINT UNSIGNED NULL COMMENT 'SEC SIC code',
  sic_description       VARCHAR(255) NULL COMMENT 'SEC SIC description',
  entity_type           VARCHAR(32) NULL COMMENT 'SEC entity type',
  fiscal_year_end       CHAR(4) NULL COMMENT 'Fiscal year end as MMDD',
  state_of_incorporation VARCHAR(8) NULL COMMENT 'State of incorporation',
  in_universe           TINYINT(1) NOT NULL DEFAULT 1 COMMENT '1 if currently tracked; delisted companies stay with 0',
  universe_source       VARCHAR(255) NULL COMMENT 'Where the company came from (universe file name or tickers)',
  submissions_sha256    CHAR(64) NULL COMMENT 'Raw object of the last submissions JSON processed',
  submissions_synced_at DATETIME(6) NULL COMMENT 'UTC time submissions were last processed',
  facts_sha256          CHAR(64) NULL COMMENT 'Raw object of the last companyfacts JSON processed',
  facts_synced_at       DATETIME(6) NULL COMMENT 'UTC time companyfacts were last processed',
  created_at            DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'Row creation (UTC)',
  updated_at            DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'Last change (UTC)',
  PRIMARY KEY (company_id),
  UNIQUE KEY uq_cik (cik),
  KEY ix_ticker (ticker)
) {T} COMMENT='Company master. One row per SEC registrant (CIK).'""",
    ),
    (
        "ref_company_ticker_history",
        f"""
CREATE TABLE ref_company_ticker_history (
  company_id  INT UNSIGNED NOT NULL COMMENT 'Company',
  ticker      VARCHAR(16) NOT NULL COMMENT 'Ticker seen in SEC submissions',
  exchange    VARCHAR(32) NULL COMMENT 'Exchange for that ticker',
  first_seen  DATE NOT NULL COMMENT 'First date we saw it',
  last_seen   DATE NOT NULL COMMENT 'Most recent date we saw it',
  PRIMARY KEY (company_id, ticker),
  CONSTRAINT fk_tick_company FOREIGN KEY (company_id) REFERENCES ref_company (company_id)
) {T} COMMENT='Tickers over time, so old client files still resolve after a ticker change.'""",
    ),
    (
        "ref_company_name_history",
        f"""
CREATE TABLE ref_company_name_history (
  company_id  INT UNSIGNED NOT NULL COMMENT 'Company',
  name        VARCHAR(255) NOT NULL COMMENT 'Former name per SEC',
  valid_from  DATE NULL COMMENT 'Start of use',
  valid_to    DATE NULL COMMENT 'End of use',
  PRIMARY KEY (company_id, name),
  CONSTRAINT fk_name_company FOREIGN KEY (company_id) REFERENCES ref_company (company_id)
) {T} COMMENT='Former company names from SEC submissions.'""",
    ),
    (
        "ref_ir_source",
        f"""
CREATE TABLE ref_ir_source (
  source_id             INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  company_id            INT UNSIGNED NOT NULL COMMENT 'Company',
  source_type           ENUM('ir_home','results_page','supplemental_pdf','email_alerts','sec_edgar','rss','other') NOT NULL COMMENT 'What this URL is',
  url                   VARCHAR(2048) NOT NULL COMMENT 'URL to watch or fetch',
  url_hash              CHAR(64) AS (SHA2(url, 256)) STORED COMMENT 'Hash of url, for uniqueness',
  check_method          ENUM('html','pdf','rss','email','sec','manual') NOT NULL COMMENT 'How the watcher should check it',
  is_active             TINYINT(1) NOT NULL DEFAULT 1 COMMENT '0 when the URL is retired; rows are never deleted',
  verified              TINYINT(1) NULL COMMENT 'Verified by a person per the universe file',
  last_checked_at       DATETIME(6) NULL COMMENT 'UTC time of the last check',
  last_changed_at       DATETIME(6) NULL COMMENT 'UTC time the content last changed',
  last_sha256           CHAR(64) NULL COMMENT 'Raw object of the last successful check',
  consecutive_failures  SMALLINT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'Failed checks in a row; the monitor alerts past a threshold',
  notes                 VARCHAR(1024) NULL COMMENT 'Free-text notes',
  PRIMARY KEY (source_id),
  UNIQUE KEY uq_source (company_id, source_type, url_hash),
  CONSTRAINT fk_irs_company FOREIGN KEY (company_id) REFERENCES ref_company (company_id)
) {T} COMMENT='IR website, supplemental and alert-signup URLs per company. Input to the IR fetcher and inbox.'""",
    ),
    (
        "ref_metric",
        f"""
CREATE TABLE ref_metric (
  metric_id         SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  metric_code       VARCHAR(48) NOT NULL COMMENT 'Stable public code used in client files, e.g. FFO_PER_SHARE',
  name              VARCHAR(128) NOT NULL COMMENT 'Display name',
  category          VARCHAR(48) NOT NULL COMMENT 'Grouping, e.g. Earnings, Balance Sheet',
  unit_type         ENUM('USD','USD_PER_SHARE','PCT','SF','COUNT','SHARES','RATIO','YEARS','USD_PER_SF') NOT NULL COMMENT 'Unit family',
  period_nature     ENUM('duration','instant') NOT NULL COMMENT 'Flow over a period or balance at a date',
  is_non_gaap       TINYINT(1) NOT NULL DEFAULT 0 COMMENT '1 for non-GAAP measures',
  is_nareit_defined TINYINT(1) NOT NULL DEFAULT 0 COMMENT '1 if Nareit publishes the definition',
  definition        TEXT NULL COMMENT 'Definition shown to clients',
  is_active         TINYINT(1) NOT NULL DEFAULT 1 COMMENT '0 once removed from the seed file; history stays',
  updated_at        DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'Last change (UTC)',
  PRIMARY KEY (metric_id),
  UNIQUE KEY uq_code (metric_code)
) {T} COMMENT='Standard REIT metric dictionary. Seeded from src/reitstreet/reference/data/metrics.csv.'""",
    ),
    (
        "ref_metric_map",
        f"""
CREATE TABLE ref_metric_map (
  map_id        INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  map_key       VARCHAR(191) NOT NULL COMMENT 'Stable key from the seed file (or manual:<n>) so edits upsert cleanly',
  metric_id     SMALLINT UNSIGNED NOT NULL COMMENT 'Target metric',
  source_type   ENUM('xbrl','supplemental') NOT NULL COMMENT 'Which staging data the rule reads',
  taxonomy      VARCHAR(32) NULL COMMENT 'XBRL taxonomy (xbrl rules)',
  tag           VARCHAR(255) NULL COMMENT 'XBRL tag (xbrl rules)',
  unit          VARCHAR(32) NULL COMMENT 'XBRL unit filter; NULL = any',
  label_regex   VARCHAR(512) NULL COMMENT 'Row label pattern, MySQL REGEXP, case-insensitive (supplemental rules)',
  exclude_regex VARCHAR(512) NULL COMMENT 'Row labels matching this are excluded',
  segment_regex VARCHAR(512) NULL COMMENT 'Optional segment label filter',
  priority      SMALLINT UNSIGNED NOT NULL DEFAULT 100 COMMENT 'Lower wins when several rules match the same period',
  multiplier    DECIMAL(18,8) NOT NULL DEFAULT 1 COMMENT 'Applied to the source value (e.g. 1000 for thousands)',
  company_id    INT UNSIGNED NULL COMMENT 'NULL = applies to all; set = company-specific override',
  valid_from    DATE NULL COMMENT 'Rule applies to periods ending on or after',
  valid_to      DATE NULL COMMENT 'Rule applies to periods ending on or before',
  is_active     TINYINT(1) NOT NULL DEFAULT 1 COMMENT '0 = disabled (kept for audit)',
  origin        ENUM('seed','manual') NOT NULL DEFAULT 'seed' COMMENT 'seed rows are managed by the seed command; manual rows never touched by it',
  notes         VARCHAR(512) NULL COMMENT 'Why this rule exists',
  updated_at    DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'Last change (UTC)',
  PRIMARY KEY (map_id),
  UNIQUE KEY uq_key (map_key),
  KEY ix_tag (taxonomy, tag),
  CONSTRAINT fk_map_metric FOREIGN KEY (metric_id) REFERENCES ref_metric (metric_id),
  CONSTRAINT fk_map_company FOREIGN KEY (company_id) REFERENCES ref_company (company_id)
) {T} COMMENT='Rules mapping XBRL tags and supplemental row labels to metrics. Mappings are data, not code.'""",
    ),
    # ====================================================================== stg
    (
        "stg_sec_filing",
        f"""
CREATE TABLE stg_sec_filing (
  filing_id               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  company_id              INT UNSIGNED NOT NULL COMMENT 'Filer',
  accession_no            CHAR(20) NOT NULL COMMENT 'SEC accession number 0000000000-00-000000',
  form_type               VARCHAR(32) NOT NULL COMMENT 'Form, e.g. 10-K, 10-Q, 8-K, 10-K/A',
  filing_date             DATE NOT NULL COMMENT 'Date filed with SEC (when the market could know it)',
  report_date             DATE NULL COMMENT 'Period of report',
  acceptance_datetime     DATETIME NULL COMMENT 'EDGAR acceptance timestamp',
  act                     VARCHAR(16) NULL COMMENT 'Securities act',
  file_number             VARCHAR(32) NULL COMMENT 'SEC file number',
  film_number             VARCHAR(32) NULL COMMENT 'SEC film number',
  items                   VARCHAR(255) NULL COMMENT '8-K item numbers, e.g. 2.02,9.01',
  size_bytes              BIGINT UNSIGNED NULL COMMENT 'Filing size per SEC',
  is_xbrl                 TINYINT(1) NULL COMMENT 'Has XBRL',
  is_inline_xbrl          TINYINT(1) NULL COMMENT 'Has inline XBRL',
  primary_document        VARCHAR(255) NULL COMMENT 'Primary document file name',
  primary_doc_description VARCHAR(255) NULL COMMENT 'Primary document description',
  index_url               VARCHAR(512) NOT NULL COMMENT 'EDGAR filing index page',
  primary_doc_url         VARCHAR(512) NULL COMMENT 'URL of the primary document',
  is_amendment            TINYINT(1) AS (form_type LIKE '%/A') STORED COMMENT '1 for amended filings',
  src_sha256              CHAR(64) NOT NULL COMMENT 'Raw submissions JSON this row was parsed from',
  parser_version          VARCHAR(16) NOT NULL COMMENT 'Parser version that produced the row',
  first_seen_run_id       BIGINT UNSIGNED NULL COMMENT 'Run that first loaded it',
  docs_indexed_at         DATETIME(6) NULL COMMENT 'UTC time its index page was processed; NULL = not yet',
  docs_index_sha256       CHAR(64) NULL COMMENT 'Raw object of the index page',
  created_at              DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'Row creation (UTC)',
  PRIMARY KEY (filing_id),
  UNIQUE KEY uq_acc (company_id, accession_no),
  KEY ix_form_date (form_type, filing_date),
  KEY ix_acc (accession_no),
  CONSTRAINT fk_filing_company FOREIGN KEY (company_id) REFERENCES ref_company (company_id)
) {T} COMMENT='Every SEC filing of every tracked company, from data.sec.gov submissions.'""",
    ),
    (
        "stg_sec_filing_document",
        f"""
CREATE TABLE stg_sec_filing_document (
  filing_id      BIGINT UNSIGNED NOT NULL COMMENT 'Filing',
  document_name  VARCHAR(255) NOT NULL COMMENT 'File name inside the filing',
  seq            SMALLINT UNSIGNED NULL COMMENT 'Sequence number on the index page',
  doc_type       VARCHAR(64) NULL COMMENT 'Document type, e.g. 10-Q, EX-99.1, EX-99.2',
  description    VARCHAR(512) NULL COMMENT 'Description on the index page',
  url            VARCHAR(512) NOT NULL COMMENT 'Document URL',
  size_bytes     BIGINT UNSIGNED NULL COMMENT 'Size per index page',
  src_sha256     CHAR(64) NOT NULL COMMENT 'Raw index page this row was parsed from',
  PRIMARY KEY (filing_id, document_name),
  KEY ix_type (doc_type),
  CONSTRAINT fk_fdoc_filing FOREIGN KEY (filing_id) REFERENCES stg_sec_filing (filing_id)
) {T} COMMENT='Documents listed on EDGAR filing index pages (earnings 8-Ks, 10-Ks, 10-Qs).'""",
    ),
    (
        "stg_xbrl_concept",
        f"""
CREATE TABLE stg_xbrl_concept (
  concept_id   INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  taxonomy     VARCHAR(32) NOT NULL COMMENT 'us-gaap, dei, srt, ...',
  tag          VARCHAR(255) NOT NULL COMMENT 'XBRL element name',
  label        VARCHAR(512) NULL COMMENT 'Standard label',
  description  TEXT NULL COMMENT 'Taxonomy documentation',
  PRIMARY KEY (concept_id),
  UNIQUE KEY uq_tag (taxonomy, tag)
) {T} COMMENT='XBRL concepts seen in companyfacts.'""",
    ),
    (
        "stg_xbrl_fact",
        f"""
CREATE TABLE stg_xbrl_fact (
  fact_id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  company_id        INT UNSIGNED NOT NULL COMMENT 'Reporting company',
  concept_id        INT UNSIGNED NOT NULL COMMENT 'Concept',
  unit              VARCHAR(32) NOT NULL COMMENT 'Unit, e.g. USD, USD/shares, shares',
  period_start      DATE NULL COMMENT 'Start for duration facts; NULL for instants',
  period_end        DATE NOT NULL COMMENT 'End date or instant date',
  period_start_key  DATE AS (COALESCE(period_start, '1900-01-01')) STORED COMMENT 'Non-null period_start for keys',
  duration_days     SMALLINT AS (IF(period_start IS NULL, NULL, DATEDIFF(period_end, period_start) + 1)) STORED COMMENT 'Length of a duration fact in days',
  value             DECIMAL(28,6) NOT NULL COMMENT 'Reported value',
  accession_no      CHAR(20) NOT NULL COMMENT 'Filing that reported this value (the same period recurs in later filings)',
  fy                SMALLINT NULL COMMENT 'Fiscal year of the reporting filing (SEC field, not the period)',
  fp                VARCHAR(4) NULL COMMENT 'Fiscal period of the reporting filing (SEC field)',
  form              VARCHAR(32) NULL COMMENT 'Form of the reporting filing',
  filed             DATE NOT NULL COMMENT 'Filing date of the reporting filing = when the value became known',
  frame             VARCHAR(16) NULL COMMENT 'SEC frame, e.g. CY2024Q1',
  src_sha256        CHAR(64) NOT NULL COMMENT 'Raw companyfacts JSON this row was first parsed from',
  first_seen_run_id BIGINT UNSIGNED NULL COMMENT 'Run that first loaded it',
  PRIMARY KEY (fact_id),
  UNIQUE KEY uq_fact (company_id, concept_id, unit, period_start_key, period_end, accession_no),
  KEY ix_company_end (company_id, period_end),
  CONSTRAINT fk_fact_company FOREIGN KEY (company_id) REFERENCES ref_company (company_id),
  CONSTRAINT fk_fact_concept FOREIGN KEY (concept_id) REFERENCES stg_xbrl_concept (concept_id)
) {T} COMMENT='One row per value per reporting filing. Restatements are new rows, never updates.'""",
    ),
    (
        "stg_document",
        f"""
CREATE TABLE stg_document (
  doc_id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  company_id          INT UNSIGNED NULL COMMENT 'Company; NULL until identified (archive imports)',
  doc_kind            ENUM('supplemental','earnings_release','presentation','exhibit_other','unknown') NOT NULL COMMENT 'What the document is',
  source              ENUM('sec_8k_exhibit','ir_website','email','manual_archive') NOT NULL COMMENT 'How we got it',
  source_url          VARCHAR(2048) NOT NULL COMMENT 'Where it came from (file:// URI for archive imports)',
  source_url_hash     CHAR(64) AS (SHA2(source_url, 256)) STORED COMMENT 'Hash of source_url, for uniqueness',
  accession_no        CHAR(20) NULL COMMENT 'SEC accession, for 8-K exhibits',
  filing_id           BIGINT UNSIGNED NULL COMMENT 'SEC filing, for 8-K exhibits',
  original_filename   VARCHAR(255) NULL COMMENT 'File name as published',
  sha256              CHAR(64) NULL COMMENT 'Raw object with the bytes; NULL until fetched',
  bytes               BIGINT UNSIGNED NULL COMMENT 'Size',
  mime_type           VARCHAR(128) NULL COMMENT 'MIME type',
  period_end_date     DATE NULL COMMENT 'Reporting period end the document covers',
  published_date      DATE NULL COMMENT 'Date published (filing date for 8-Ks) = when the market could know it',
  status              ENUM('registered','fetched','parsed','needs_review','failed','duplicate','skipped') NOT NULL DEFAULT 'registered' COMMENT 'Lifecycle state',
  duplicate_of_doc_id BIGINT UNSIGNED NULL COMMENT 'Earlier document with identical bytes',
  current_parse_id    BIGINT UNSIGNED NULL COMMENT 'Parse whose output the warehouse uses',
  status_note         VARCHAR(1024) NULL COMMENT 'Reason for needs_review, failed, skipped',
  registered_run_id   BIGINT UNSIGNED NULL COMMENT 'Run that registered it',
  created_at          DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'Row creation (UTC)',
  updated_at          DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'Last change (UTC)',
  PRIMARY KEY (doc_id),
  UNIQUE KEY uq_source (source, source_url_hash),
  KEY ix_sha (sha256),
  KEY ix_company_period (company_id, period_end_date),
  KEY ix_status (status),
  CONSTRAINT fk_doc_company FOREIGN KEY (company_id) REFERENCES ref_company (company_id),
  CONSTRAINT fk_doc_object FOREIGN KEY (sha256) REFERENCES raw_object (sha256)
) {T} COMMENT='Every supplemental, earnings release and other document from every source, one registry.'""",
    ),
    (
        "stg_doc_parse",
        f"""
CREATE TABLE stg_doc_parse (
  parse_id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key of one parse attempt',
  doc_id          BIGINT UNSIGNED NOT NULL COMMENT 'Document parsed',
  sha256          CHAR(64) NOT NULL COMMENT 'Exact bytes parsed',
  parser_name     VARCHAR(64) NOT NULL COMMENT 'Parser used',
  parser_version  VARCHAR(16) NOT NULL COMMENT 'Parser version',
  run_id          BIGINT UNSIGNED NULL COMMENT 'Job run',
  status          ENUM('running','succeeded','failed') NOT NULL COMMENT 'Outcome',
  n_tables        INT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'Tables extracted',
  n_items         INT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'Line items extracted',
  started_at      DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'UTC start',
  finished_at     DATETIME(6) NULL COMMENT 'UTC end',
  error_message   TEXT NULL COMMENT 'Error, when failed',
  PRIMARY KEY (parse_id),
  KEY ix_doc (doc_id),
  CONSTRAINT fk_parse_doc FOREIGN KEY (doc_id) REFERENCES stg_document (doc_id)
) {T} COMMENT='Every parse of every document. Old parses are kept, so parser upgrades can be compared and rolled back.'""",
    ),
    (
        "stg_doc_table",
        f"""
CREATE TABLE stg_doc_table (
  table_id     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  parse_id     BIGINT UNSIGNED NOT NULL COMMENT 'Parse that produced it',
  doc_id       BIGINT UNSIGNED NOT NULL COMMENT 'Document',
  page_no      INT UNSIGNED NOT NULL COMMENT 'Page number, 1-based',
  table_index  SMALLINT UNSIGNED NOT NULL COMMENT 'Order of the table on the page, 1-based',
  title        VARCHAR(512) NULL COMMENT 'Table heading as printed',
  section      VARCHAR(255) NULL COMMENT 'Section of the document, e.g. Debt Summary',
  bbox         JSON NULL COMMENT 'Position on the page [x0, y0, x1, y1] in PDF points',
  PRIMARY KEY (table_id),
  KEY ix_parse (parse_id),
  CONSTRAINT fk_tbl_parse FOREIGN KEY (parse_id) REFERENCES stg_doc_parse (parse_id)
) {T} COMMENT='Tables found in parsed documents.'""",
    ),
    (
        "stg_doc_line_item",
        f"""
CREATE TABLE stg_doc_line_item (
  item_id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  parse_id       BIGINT UNSIGNED NOT NULL COMMENT 'Parse that produced it',
  table_id       BIGINT UNSIGNED NULL COMMENT 'Table it sits in',
  doc_id         BIGINT UNSIGNED NOT NULL COMMENT 'Document',
  page_no        INT UNSIGNED NOT NULL COMMENT 'Page number, 1-based',
  row_index      SMALLINT UNSIGNED NULL COMMENT 'Row in the table, 1-based',
  col_index      SMALLINT UNSIGNED NULL COMMENT 'Column in the table, 1-based',
  row_label      VARCHAR(512) NOT NULL COMMENT 'Row label as printed',
  col_label      VARCHAR(255) NULL COMMENT 'Column header as printed',
  raw_text       VARCHAR(255) NULL COMMENT 'Cell text as printed, e.g. (1,234)',
  value_numeric  DECIMAL(28,6) NULL COMMENT 'Parsed number after sign and scale',
  scale_hint     VARCHAR(32) NULL COMMENT 'Scale stated on the page, e.g. in thousands',
  unit_hint      VARCHAR(32) NULL COMMENT 'Unit stated, e.g. $, %, sf',
  period_start   DATE NULL COMMENT 'Period start the column refers to',
  period_end     DATE NULL COMMENT 'Period end the column refers to',
  segment_label  VARCHAR(255) NULL COMMENT 'Segment, region or property type the row refers to',
  confidence     DECIMAL(4,3) NULL COMMENT 'Parser confidence 0..1',
  PRIMARY KEY (item_id),
  KEY ix_parse (parse_id),
  KEY ix_doc (doc_id),
  CONSTRAINT fk_item_parse FOREIGN KEY (parse_id) REFERENCES stg_doc_parse (parse_id)
) {T} COMMENT='Individual values extracted from document tables, with page and cell position.'""",
    ),
    # ====================================================================== wh
    (
        "wh_metric_value",
        f"""
CREATE TABLE wh_metric_value (
  value_id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  company_id        INT UNSIGNED NOT NULL COMMENT 'Company',
  metric_id         SMALLINT UNSIGNED NOT NULL COMMENT 'Metric',
  period_type       ENUM('Q','YTD','FY','TTM','INSTANT') NOT NULL COMMENT 'Q quarter, YTD year to date, FY fiscal year, INSTANT balance',
  period_start      DATE NULL COMMENT 'Business period start (valid time); NULL for INSTANT',
  period_end        DATE NOT NULL COMMENT 'Business period end or balance date (valid time)',
  period_start_key  DATE AS (COALESCE(period_start, '1900-01-01')) STORED COMMENT 'Non-null period_start for keys',
  fiscal_year       SMALLINT NOT NULL COMMENT 'Fiscal year (named for the calendar year it ends in)',
  fiscal_quarter    TINYINT NULL COMMENT '1..4 for Q, YTD and INSTANT; NULL for FY',
  segment           VARCHAR(128) NOT NULL DEFAULT 'Total' COMMENT 'Total or a segment label',
  value             DECIMAL(28,6) NOT NULL COMMENT 'Value in unit',
  unit              VARCHAR(32) NOT NULL COMMENT 'Unit, e.g. USD, USD/shares, pct',
  source_type       ENUM('xbrl','derived','supplemental','manual') NOT NULL COMMENT 'xbrl: as tagged; derived: computed from xbrl (e.g. Q4 = FY - 9M); supplemental: parsed from a document; manual: analyst entry',
  version           SMALLINT UNSIGNED NOT NULL COMMENT '1 = first reported, 2+ = restatements (knowledge time order)',
  known_from        DATE NOT NULL COMMENT 'Knowledge time: date the value was published (filing or document date)',
  known_to          DATE NULL COMMENT 'Knowledge time: date a different value was published; NULL = still the latest',
  source_accession  CHAR(20) NULL COMMENT 'SEC filing the value comes from',
  source_fact_id    BIGINT UNSIGNED NULL COMMENT 'stg_xbrl_fact row (xbrl)',
  source_doc_id     BIGINT UNSIGNED NULL COMMENT 'stg_document row (supplemental)',
  source_item_id    BIGINT UNSIGNED NULL COMMENT 'stg_doc_line_item row (supplemental)',
  source_page_no    INT UNSIGNED NULL COMMENT 'Page in the source document',
  source_table_id   BIGINT UNSIGNED NULL COMMENT 'stg_doc_table row',
  source_sha256     CHAR(64) NULL COMMENT 'Raw object behind the value',
  derivation        VARCHAR(255) NULL COMMENT 'How a derived value was computed, with the facts used',
  row_hash          CHAR(64) NOT NULL COMMENT 'Hash of key, knowledge time, value and lineage; equal hash = identical row',
  recorded_at       DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'System time: when this row entered the warehouse',
  recorded_run_id   BIGINT UNSIGNED NULL COMMENT 'Build run that inserted it',
  retired_at        DATETIME(6) NULL COMMENT 'System time: when a rebuild replaced it (mapping or parser change); NULL = live',
  retired_run_id    BIGINT UNSIGNED NULL COMMENT 'Build run that retired it',
  is_live           TINYINT AS (IF(retired_at IS NULL, 1, NULL)) STORED COMMENT '1 while live, NULL once retired',
  PRIMARY KEY (value_id),
  UNIQUE KEY uq_live (company_id, metric_id, period_type, period_start_key, period_end, segment, source_type, known_from, is_live),
  UNIQUE KEY uq_hash_live (row_hash, is_live),
  KEY ix_lookup (company_id, metric_id, period_end),
  KEY ix_known (known_from),
  CONSTRAINT fk_mv_company FOREIGN KEY (company_id) REFERENCES ref_company (company_id),
  CONSTRAINT fk_mv_metric FOREIGN KEY (metric_id) REFERENCES ref_metric (metric_id),
  CONSTRAINT ck_mv_known CHECK (known_to IS NULL OR known_to > known_from),
  CONSTRAINT ck_mv_quarter CHECK (fiscal_quarter IS NULL OR fiscal_quarter BETWEEN 1 AND 4)
) {T} COMMENT='Curated metric values, bitemporal: valid time (period), knowledge time (known_from/to) and system time (recorded/retired). Rows are retired, never deleted.'""",
    ),
    (
        "wh_property",
        f"""
CREATE TABLE wh_property (
  property_id     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  company_id      INT UNSIGNED NOT NULL COMMENT 'Current owner',
  property_key    CHAR(64) NOT NULL COMMENT 'Hash of normalized name and address, for matching across documents',
  name            VARCHAR(255) NOT NULL COMMENT 'Property name as reported',
  address         VARCHAR(255) NULL COMMENT 'Street address',
  city            VARCHAR(128) NULL COMMENT 'City',
  state           VARCHAR(64) NULL COMMENT 'State or province',
  postal_code     VARCHAR(16) NULL COMMENT 'Postal code',
  country         CHAR(2) NULL COMMENT 'ISO country code',
  market          VARCHAR(128) NULL COMMENT 'Market or MSA as reported',
  property_type   VARCHAR(64) NULL COMMENT 'Property type',
  latitude        DECIMAL(9,6) NULL COMMENT 'Latitude',
  longitude       DECIMAL(9,6) NULL COMMENT 'Longitude',
  first_doc_id    BIGINT UNSIGNED NULL COMMENT 'Document where first seen',
  created_at      DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'Row creation (UTC)',
  PRIMARY KEY (property_id),
  UNIQUE KEY uq_key (company_id, property_key),
  CONSTRAINT fk_prop_company FOREIGN KEY (company_id) REFERENCES ref_company (company_id)
) {T} COMMENT='Property master, filled by the supplemental parser.'""",
    ),
    (
        "wh_property_snapshot",
        f"""
CREATE TABLE wh_property_snapshot (
  snapshot_id     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  property_id     BIGINT UNSIGNED NOT NULL COMMENT 'Property',
  as_of_date      DATE NOT NULL COMMENT 'Date the figures describe (valid time)',
  square_feet     DECIMAL(16,2) NULL COMMENT 'Rentable square feet',
  units           INT UNSIGNED NULL COMMENT 'Units, keys or beds',
  occupancy_pct   DECIMAL(7,4) NULL COMMENT 'Occupancy percent',
  annualized_rent DECIMAL(20,2) NULL COMMENT 'Annualized base rent, USD',
  ownership_pct   DECIMAL(7,4) NULL COMMENT 'Company ownership share',
  known_from      DATE NOT NULL COMMENT 'Knowledge time: publication date of the source document',
  source_doc_id   BIGINT UNSIGNED NOT NULL COMMENT 'Source document',
  source_item_id  BIGINT UNSIGNED NULL COMMENT 'Source line item',
  source_page_no  INT UNSIGNED NULL COMMENT 'Source page',
  recorded_at     DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'System time inserted',
  retired_at      DATETIME(6) NULL COMMENT 'System time replaced; NULL = live',
  PRIMARY KEY (snapshot_id),
  KEY ix_prop (property_id, as_of_date),
  CONSTRAINT fk_snap_prop FOREIGN KEY (property_id) REFERENCES wh_property (property_id)
) {T} COMMENT='Property figures per reporting date, with lineage to page and cell.'""",
    ),
    (
        "wh_debt_maturity",
        f"""
CREATE TABLE wh_debt_maturity (
  maturity_id     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  company_id      INT UNSIGNED NOT NULL COMMENT 'Company',
  as_of_date      DATE NOT NULL COMMENT 'Balance sheet date of the schedule',
  maturity_year   SMALLINT NOT NULL COMMENT 'Year the debt matures (or first year of a Thereafter bucket)',
  debt_type       VARCHAR(64) NOT NULL DEFAULT 'Total' COMMENT 'Secured, unsecured, line of credit, Total',
  amount          DECIMAL(20,2) NOT NULL COMMENT 'Principal maturing, USD',
  wtd_avg_rate    DECIMAL(7,4) NULL COMMENT 'Weighted average rate of that bucket, percent',
  known_from      DATE NOT NULL COMMENT 'Knowledge time: publication date',
  source_doc_id   BIGINT UNSIGNED NOT NULL COMMENT 'Source document',
  source_page_no  INT UNSIGNED NULL COMMENT 'Source page',
  recorded_at     DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'System time inserted',
  retired_at      DATETIME(6) NULL COMMENT 'System time replaced; NULL = live',
  PRIMARY KEY (maturity_id),
  KEY ix_company (company_id, as_of_date),
  CONSTRAINT fk_debt_company FOREIGN KEY (company_id) REFERENCES ref_company (company_id)
) {T} COMMENT='Debt maturity ladders from supplementals.'""",
    ),
]


def upgrade() -> None:
    for _name, ddl in TABLES:
        op.execute(ddl)


def downgrade() -> None:
    for name, _ddl in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {name}")
