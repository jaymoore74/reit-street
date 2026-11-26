# Data dictionary

Generated from the database schema at Alembic revision `0006` by `reitstreet db-docs`. Do not edit by hand: change the COMMENT in a migration and regenerate.

## ops_* Operations: runs, checkpoints, dead letters, data quality, alerts

- [`ops_alert`](#ops_alert): Every alert raised, sent or suppressed.
- [`ops_dead_letter`](#ops_dead_letter): Dead-letter / QA queue: anything that could not be processed. Nothing is silently dropped.
- [`ops_dq_result`](#ops_dq_result): Data quality results. One summary pass row per check per run plus one row per failure.
- [`ops_ir_check`](#ops_ir_check): One row per company per IR fetch run. Source of coverage reporting and IR alerts.
- [`ops_ir_coverage`](#ops_ir_coverage) (view): VIEW
- [`ops_job_item`](#ops_job_item): Per-item checkpoints. --resume <run_id> skips items that finished ok in that run.
- [`ops_job_run`](#ops_job_run): One row per job execution. The monitor alerts on failed, partial, aborted and overdue runs.
- [`ops_parse_quarantine`](#ops_parse_quarantine) (view): VIEW

### ops_alert

Table. Every alert raised, sent or suppressed.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `alert_id` | bigint unsigned | NO | PRI | Surrogate key |
| `created_at` | datetime(6) | NO |  | UTC time raised |
| `severity` | enum('info','warn','error','critical') | NO |  | Alert severity |
| `source` | varchar(64) | NO |  | Who raised it: job name, monitor, dq |
| `title` | varchar(255) | NO |  | One-line summary |
| `body` | text | YES |  | Details |
| `dedupe_key` | varchar(191) | NO | MUL | Same key within the suppression window is not re-sent |
| `channels` | varchar(128) | YES |  | Channels it was delivered to (log, email, webhook) |
| `suppressed` | tinyint(1) | NO |  | 1 if not sent because an identical alert went out recently |
| `delivery_error` | text | YES |  | Error from a channel that failed to deliver |

### ops_dead_letter

Table. Dead-letter / QA queue: anything that could not be processed. Nothing is silently dropped.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `dl_id` | bigint unsigned | NO | PRI | Surrogate key |
| `stage` | varchar(64) | NO | MUL | Pipeline stage that failed, e.g. sec.companyfacts.parse, supplemental.parse |
| `item_key` | varchar(191) | NO |  | Work item that failed (same key as ops_job_item) |
| `sha256` | char(64) | YES |  | Raw object being processed, when there is one (re-parse it with replay) |
| `sha_key` | char(64) (generated) | YES |  | Non-null copy of sha256 for the uniqueness rule |
| `company_id` | int unsigned | YES |  | Company concerned, when known |
| `doc_id` | bigint unsigned | YES |  | Document concerned, when known |
| `error_class` | varchar(128) | NO |  | Python exception class |
| `error_message` | text | NO |  | Exception message |
| `traceback` | mediumtext | YES |  | Full traceback for the engineer fixing it |
| `status` | enum('open','resolved','ignored') | NO | MUL | open: needs attention; resolved: fixed and reprocessed; ignored: accepted as unfixable |
| `is_open` | tinyint (generated) | YES |  | 1 while open, NULL otherwise (lets resolved history repeat) |
| `occurrences` | int unsigned | NO |  | Times this same failure was hit while open |
| `first_run_id` | bigint unsigned | YES |  | Run that first hit it |
| `last_run_id` | bigint unsigned | YES |  | Most recent run that hit it |
| `first_seen_at` | datetime(6) | NO |  | UTC first failure |
| `last_seen_at` | datetime(6) | NO |  | UTC latest failure |
| `resolved_at` | datetime(6) | YES |  | UTC time it was resolved or ignored |
| `resolution_note` | varchar(1024) | YES |  | What was done about it |

### ops_dq_result

Table. Data quality results. One summary pass row per check per run plus one row per failure.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `result_id` | bigint unsigned | NO | PRI | Surrogate key |
| `run_id` | bigint unsigned | YES | MUL | dq run that produced it |
| `check_code` | varchar(64) | NO | MUL | Check identifier, documented in reitstreet.quality.checks |
| `severity` | enum('info','warn','error') | NO |  | How bad a failure of this check is |
| `status` | enum('pass','fail','error') | NO |  | pass, fail (data problem) or error (the check itself crashed) |
| `company_id` | int unsigned | YES | MUL | Company the result is about, if any |
| `subject` | varchar(255) | YES |  | What was checked, e.g. TOTAL_ASSETS 2024-12-31 |
| `observed` | varchar(255) | YES |  | Observed value |
| `expected` | varchar(255) | YES |  | Expected value or tolerance |
| `details` | json | YES |  | Extra context (ids, values) for investigation |
| `checked_at` | datetime(6) | NO |  | UTC time of the check |

### ops_ir_check

Table. One row per company per IR fetch run. Source of coverage reporting and IR alerts.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `check_id` | bigint unsigned | NO | PRI | Surrogate key |
| `run_id` | bigint unsigned | YES | MUL | Job run |
| `company_id` | int unsigned | NO | MUL | Company |
| `source_id` | int unsigned | YES |  | ref_ir_source page checked; NULL when the company has none |
| `strategy` | varchar(16) | NO |  | Strategy that produced this outcome |
| `outcome` | enum('ok','not_modified','no_documents','js_only','layout_break','robots_blocked','http_error','needs_headless','not_configured','no_source') | NO |  | What happened |
| `http_status` | smallint | YES |  | Status of the page request |
| `links_found` | int unsigned | NO |  | Document links on the page |
| `supp_links` | int unsigned | NO |  | Of which classified supplemental |
| `new_links` | int unsigned | NO |  | Links not seen before |
| `new_docs` | int unsigned | NO |  | Documents downloaded and registered |
| `latest_supp_period` | date | YES |  | Latest supplemental period end seen on the page |
| `note` | varchar(1024) | YES |  | Detail |
| `checked_at` | datetime(6) | NO |  | UTC time |

### ops_ir_coverage

View.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `company_id` | int unsigned | YES |  | Surrogate key used everywhere |
| `ticker` | varchar(16) | YES |  | Current primary ticker |
| `name` | varchar(255) | YES |  | Current SEC conformed name |
| `reit_type` | varchar(32) | YES |  | Equity, mortgage or hybrid |
| `strategy` | varchar(11) | NO |  |  |
| `platform` | varchar(32) | YES |  | IR site vendor or layout family, e.g. q4cdn, static-files, cloudfront, wordpress |
| `expects_supplemental` | int | NO |  |  |
| `probe_status` | varchar(14) | NO |  |  |
| `probe_supp_links` | smallint unsigned | YES |  | Supplemental links the probe saw on the results page |
| `probe_latest_period` | varchar(16) | YES |  | Latest supplemental period the probe saw, e.g. Q3 2026 |
| `probe_date` | date | YES |  | When the probe ran |
| `last_checked_at` | datetime(6) | YES |  | UTC time |
| `last_strategy` | varchar(16) | YES |  | Strategy that produced this outcome |
| `last_outcome` | enum('ok','not_modified','no_documents','js_only','layout_break','robots_blocked','http_error','needs_headless','not_configured','no_source') | YES |  | What happened |
| `last_http_status` | smallint | YES |  | Status of the page request |
| `last_supp_links` | int unsigned | YES |  | Of which classified supplemental |
| `last_page_supp_period` | date | YES |  | Latest supplemental period end seen on the page |
| `latest_supplemental_held` | date | YES |  |  |
| `coverage` | varchar(14) | YES |  |  |

### ops_job_item

Table. Per-item checkpoints. --resume <run_id> skips items that finished ok in that run.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `run_id` | bigint unsigned | NO | PRI | Run this item belongs to |
| `item_key` | varchar(191) | NO | PRI | Stable key of the work item, e.g. cik:1045609 or doc:123 |
| `status` | enum('running','ok','failed','skipped') | NO |  | Outcome of the item |
| `started_at` | datetime(6) | NO |  | UTC start |
| `finished_at` | datetime(6) | YES |  | UTC end |
| `rows_written` | int unsigned | NO |  | Rows written while processing this item |
| `detail` | varchar(1024) | YES |  | Short outcome note or error message |

### ops_job_run

Table. One row per job execution. The monitor alerts on failed, partial, aborted and overdue runs.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `run_id` | bigint unsigned | NO | PRI | Surrogate key of one execution of one job |
| `job` | varchar(64) | NO | MUL | Job name, e.g. sec-sync, build-metrics, dq, import-archive |
| `args` | json | YES |  | Command line arguments the run was started with |
| `status` | enum('running','succeeded','partial','failed','aborted') | NO | MUL | succeeded: all items ok; partial: some items failed; failed: run-level failure; aborted: died without finishing (set by monitor) |
| `host` | varchar(128) | YES |  | Machine that ran the job |
| `pid` | int unsigned | YES |  | Operating system process id |
| `code_version` | varchar(64) | YES |  | Package version and git commit of the code that ran |
| `resumed_from_run_id` | bigint unsigned | YES |  | Run whose completed items this run skipped (--resume) |
| `started_at` | datetime(6) | NO |  | UTC start time |
| `heartbeat_at` | datetime(6) | YES |  | UTC time of the last progress update; a stale heartbeat means the process died |
| `finished_at` | datetime(6) | YES |  | UTC end time |
| `items_total` | int unsigned | NO |  | Work items attempted (usually companies or documents) |
| `items_ok` | int unsigned | NO |  | Items that finished without error |
| `items_failed` | int unsigned | NO |  | Items that raised an error (see ops_job_item, ops_dead_letter) |
| `items_skipped` | int unsigned | NO |  | Items skipped (unchanged source or already done in the resumed run) |
| `http_requests` | int unsigned | NO |  | HTTP requests made |
| `rows_written` | bigint unsigned | NO |  | Rows inserted or changed across all tables |
| `error_summary` | text | YES |  | First lines of the errors, for a quick look |

### ops_parse_quarantine

View.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `kind` | varchar(6) | NO |  |  |
| `id` | bigint unsigned | NO |  |  |
| `doc_id` | bigint unsigned | NO |  |  |
| `ticker` | varchar(16) | YES |  |  |
| `period_end_date` | date | YES |  |  |
| `page_no` | int unsigned | NO |  |  |
| `table_title` | varchar(512) | YES |  |  |
| `table_kind` | varchar(32) | YES |  |  |
| `label` | varchar(512) | NO |  |  |
| `detail` | varchar(255) | YES |  |  |
| `raw_text` | varchar(255) | YES |  |  |
| `confidence` | decimal(4,3) | YES |  |  |
| `quarantine_reason` | varchar(255) | YES |  |  |

## raw_* Raw layer: every byte received, content-addressed, immutable

- [`raw_fetch`](#raw_fetch): Every retrieval attempt, successful or not. Replay reads from here.
- [`raw_object`](#raw_object): Immutable content-addressed objects. Never updated or deleted by the pipeline.

### raw_fetch

Table. Every retrieval attempt, successful or not. Replay reads from here.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `fetch_id` | bigint unsigned | NO | PRI | Surrogate key of one retrieval |
| `run_id` | bigint unsigned | YES | MUL | Job run that made the request |
| `source` | varchar(32) | NO |  | Source family: sec, ir_website, email, manual_archive |
| `url` | varchar(2048) | NO |  | Requested URL (file:// URI for local imports) |
| `url_hash` | char(64) (generated) | YES | MUL | Hash of url, for indexing |
| `http_status` | smallint unsigned | YES |  | HTTP status (NULL for local files and network errors) |
| `sha256` | char(64) | YES | MUL | Content received; NULL when the request failed |
| `response_headers` | json | YES |  | Selected response headers (content-type, last-modified, etag, date) |
| `elapsed_ms` | int unsigned | YES |  | Request duration |
| `error` | text | YES |  | Error, when the request failed |
| `fetched_at` | datetime(6) | NO |  | UTC time of the request |

### raw_object

Table. Immutable content-addressed objects. Never updated or deleted by the pipeline.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `sha256` | char(64) | NO | PRI | SHA-256 of the exact bytes received; also the storage address |
| `bytes` | bigint unsigned | NO |  | Uncompressed size |
| `content_type` | varchar(128) | YES |  | Content-Type reported by the source |
| `storage_path` | varchar(255) | NO |  | Path relative to the raw store root (sha256/ab/cd/<sha>.gz) |
| `first_seen_at` | datetime(6) | NO |  | UTC time the content was first stored |

## ref_* Reference: companies, IR sources, metric dictionary and mapping rules

- [`ref_company`](#ref_company): Company master. One row per SEC registrant (CIK).
- [`ref_company_name_history`](#ref_company_name_history): Former company names from SEC submissions.
- [`ref_company_sector`](#ref_company_sector): Sectors each company invests in. Drives which metrics are relevant for it.
- [`ref_company_ticker_history`](#ref_company_ticker_history): Tickers over time, so old client files still resolve after a ticker change.
- [`ref_geocode_cache`](#ref_geocode_cache): Geocoder answers per normalized address, so each address is geocoded once.
- [`ref_ir_profile`](#ref_ir_profile): How each company IR site is fetched. Seeded from reference/data/ir_profiles.csv.
- [`ref_ir_source`](#ref_ir_source): IR website, supplemental and alert-signup URLs per company. Input to the IR fetcher and inbox.
- [`ref_metric`](#ref_metric): Standard REIT metric dictionary. Seeded from src/reitstreet/reference/data/metrics.csv.
- [`ref_metric_map`](#ref_metric_map): Rules mapping XBRL tags and supplemental row labels to metrics. Mappings are data, not code.
- [`ref_metric_sector`](#ref_metric_sector): Sector metric catalog: which metrics matter for which sector. Seeded from reference/data/metric_sectors.csv.
- [`ref_parse_override`](#ref_parse_override): Per-company supplemental parsing rules. Rules are data, so fixing one REIT never changes code.
- [`ref_property_source`](#ref_property_source): Source map: where each REIT lists its properties. Seeded from reference/data/property_sources.csv.
- [`ref_property_type`](#ref_property_type): Property types and their sectors. Seeded from reference/data/property_types.csv.
- [`ref_property_type_map`](#ref_property_type_map): Rules mapping reported property type text to ref_property_type. Seeded from reference/data/property_type_map.csv.
- [`ref_sector`](#ref_sector): Property sectors. Seeded from reference/data/sectors.csv.
- [`ref_web_host`](#ref_web_host): robots.txt state per web host, refreshed by the IR fetcher (default every 24 hours).

### ref_company

Table. Company master. One row per SEC registrant (CIK).

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `company_id` | int unsigned | NO | PRI | Surrogate key used everywhere |
| `cik` | int unsigned | NO | UNI | SEC Central Index Key (permanent company id) |
| `ticker` | varchar(16) | YES | MUL | Current primary ticker |
| `name` | varchar(255) | NO |  | Current SEC conformed name |
| `exchange` | varchar(32) | YES |  | Primary listing exchange |
| `sector` | varchar(64) | YES |  | Property sector (from the universe file) |
| `reit_type` | varchar(32) | YES |  | Equity, mortgage or hybrid |
| `listing_note` | varchar(255) | YES |  | Listing remarks from the universe file |
| `ir_platform` | varchar(64) | YES |  | IR website vendor (e.g. Q4, Notified), drives the future IR fetcher |
| `sic` | smallint unsigned | YES |  | SEC SIC code |
| `sic_description` | varchar(255) | YES |  | SEC SIC description |
| `entity_type` | varchar(32) | YES |  | SEC entity type |
| `fiscal_year_end` | char(4) | YES |  | Fiscal year end as MMDD |
| `state_of_incorporation` | varchar(8) | YES |  | State of incorporation |
| `in_universe` | tinyint(1) | NO |  | 1 if currently tracked; delisted companies stay with 0 |
| `universe_source` | varchar(255) | YES |  | Where the company came from (universe file name or tickers) |
| `submissions_sha256` | char(64) | YES |  | Raw object of the last submissions JSON processed |
| `submissions_synced_at` | datetime(6) | YES |  | UTC time submissions were last processed |
| `facts_sha256` | char(64) | YES |  | Raw object of the last companyfacts JSON processed |
| `facts_synced_at` | datetime(6) | YES |  | UTC time companyfacts were last processed |
| `created_at` | datetime(6) | NO |  | Row creation (UTC) |
| `updated_at` | datetime(6) | NO |  | Last change (UTC) |

### ref_company_name_history

Table. Former company names from SEC submissions.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `company_id` | int unsigned | NO | PRI | Company |
| `name` | varchar(255) | NO | PRI | Former name per SEC |
| `valid_from` | date | YES |  | Start of use |
| `valid_to` | date | YES |  | End of use |

### ref_company_sector

Table. Sectors each company invests in. Drives which metrics are relevant for it.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `company_id` | int unsigned | NO | PRI | Company |
| `sector_code` | varchar(32) | NO | PRI | Sector the company invests in |
| `is_primary` | tinyint(1) | NO |  | 1 for the main sector |
| `origin` | enum('seed','manual') | NO |  | seed rows come from the universe sector label; manual rows are never touched by the seed |
| `updated_at` | datetime(6) | NO |  | Last change (UTC) |

### ref_company_ticker_history

Table. Tickers over time, so old client files still resolve after a ticker change.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `company_id` | int unsigned | NO | PRI | Company |
| `ticker` | varchar(16) | NO | PRI | Ticker seen in SEC submissions |
| `exchange` | varchar(32) | YES |  | Exchange for that ticker |
| `first_seen` | date | NO |  | First date we saw it |
| `last_seen` | date | NO |  | Most recent date we saw it |

### ref_geocode_cache

Table. Geocoder answers per normalized address, so each address is geocoded once.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `address_key` | char(64) | NO | PRI | Hash of the normalized address (see properties.normalize.address_key) |
| `input_address` | varchar(512) | NO |  | Address sent to the geocoder |
| `provider` | varchar(32) | NO |  | Geocoder, e.g. census |
| `status` | enum('match','no_match','tie','error') | NO | MUL | Geocoder answer |
| `match_type` | varchar(16) | YES |  | Exact or Non_Exact (Census) |
| `matched_address` | varchar(512) | YES |  | Address the geocoder matched |
| `latitude` | decimal(9,6) | YES |  | Latitude (WGS84) |
| `longitude` | decimal(9,6) | YES |  | Longitude (WGS84) |
| `precision_level` | enum('rooftop','street','zip','city','none') | NO |  | How precise the point is |
| `provider_ref` | varchar(64) | YES |  | Provider id of the matched feature (Census TIGER line id) |
| `raw_sha256` | char(64) | YES |  | Raw object with the provider response |
| `geocoded_at` | datetime(6) | NO |  | UTC time of the answer |

### ref_ir_profile

Table. How each company IR site is fetched. Seeded from reference/data/ir_profiles.csv.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `company_id` | int unsigned | NO | PRI | Company |
| `strategy` | enum('static_html','q4_feed','headless','sec_8k','none') | NO |  | First fetch strategy to try for the results page; fallbacks follow in code |
| `platform` | varchar(32) | YES |  | IR site vendor or layout family, e.g. q4cdn, static-files, cloudfront, wordpress |
| `expects_supplemental` | tinyint(1) | NO |  | 1 if the company publishes a quarterly supplemental; drives the overdue check |
| `probe_status` | enum('works','works_headless','js_only','no_supp','error','not_probed') | NO |  | Result of the last manual coverage probe (from the seed file) |
| `probe_supp_links` | smallint unsigned | YES |  | Supplemental links the probe saw on the results page |
| `probe_latest_period` | varchar(16) | YES |  | Latest supplemental period the probe saw, e.g. Q3 2026 |
| `probe_date` | date | YES |  | When the probe ran |
| `notes` | varchar(1024) | YES |  | Free-text notes from the seed file |
| `profile_source` | varchar(255) | YES |  | Seed file and hash this row came from |
| `updated_at` | datetime(6) | NO |  | Last change (UTC) |

### ref_ir_source

Table. IR website, supplemental and alert-signup URLs per company. Input to the IR fetcher and inbox.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `source_id` | int unsigned | NO | PRI | Surrogate key |
| `company_id` | int unsigned | NO | MUL | Company |
| `source_type` | enum('ir_home','results_page','supplemental_pdf','email_alerts','sec_edgar','rss','other') | NO |  | What this URL is |
| `url` | varchar(2048) | NO |  | URL to watch or fetch |
| `url_hash` | char(64) (generated) | YES |  | Hash of url, for uniqueness |
| `check_method` | enum('html','pdf','rss','email','sec','manual') | NO |  | How the watcher should check it |
| `is_active` | tinyint(1) | NO |  | 0 when the URL is retired; rows are never deleted |
| `verified` | tinyint(1) | YES |  | Verified by a person per the universe file |
| `last_checked_at` | datetime(6) | YES |  | UTC time of the last check |
| `last_changed_at` | datetime(6) | YES |  | UTC time the content last changed |
| `last_sha256` | char(64) | YES |  | Raw object of the last successful check |
| `http_etag` | varchar(255) | YES |  | ETag of the last 200 response, sent back as If-None-Match |
| `http_last_modified` | varchar(64) | YES |  | Last-Modified of the last 200 response, sent back as If-Modified-Since |
| `last_outcome` | varchar(16) | YES |  | Outcome of the last check (see ops_ir_check.outcome) |
| `last_link_count` | int unsigned | YES |  | Supplemental links seen at the last successful page check |
| `max_link_count` | int unsigned | YES |  | Most supplemental links ever seen; zero after a positive count = layout break |
| `consecutive_failures` | smallint unsigned | NO |  | Failed checks in a row; the monitor alerts past a threshold |
| `notes` | varchar(1024) | YES |  | Free-text notes |

### ref_metric

Table. Standard REIT metric dictionary. Seeded from src/reitstreet/reference/data/metrics.csv.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `metric_id` | smallint unsigned | NO | PRI | Surrogate key |
| `metric_code` | varchar(48) | NO | UNI | Stable public code used in client files, e.g. FFO_PER_SHARE |
| `name` | varchar(128) | NO |  | Display name |
| `category` | varchar(48) | NO |  | Grouping, e.g. Earnings, Balance Sheet |
| `unit_type` | enum('USD','USD_PER_SHARE','PCT','SF','COUNT','SHARES','RATIO','YEARS','USD_PER_SF','USD_PER_UNIT','MW') | NO |  | Unit family (USD_PER_UNIT: per room, unit, bed or occupied room per night or month) |
| `period_nature` | enum('duration','instant') | NO |  | Flow over a period or balance at a date |
| `grain` | enum('company','segment','property') | NO |  | Level the metric describes: company (incl. segments of it) or a single property |
| `is_non_gaap` | tinyint(1) | NO |  | 1 for non-GAAP measures |
| `is_nareit_defined` | tinyint(1) | NO |  | 1 if Nareit publishes the definition |
| `definition` | text | YES |  | Definition shown to clients |
| `is_active` | tinyint(1) | NO |  | 0 once removed from the seed file; history stays |
| `updated_at` | datetime(6) | NO |  | Last change (UTC) |

### ref_metric_map

Table. Rules mapping XBRL tags and supplemental row labels to metrics. Mappings are data, not code.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `map_id` | int unsigned | NO | PRI | Surrogate key |
| `map_key` | varchar(191) | NO | UNI | Stable key from the seed file (or manual:<n>) so edits upsert cleanly |
| `metric_id` | smallint unsigned | NO | MUL | Target metric |
| `source_type` | enum('xbrl','supplemental') | NO |  | Which staging data the rule reads |
| `taxonomy` | varchar(32) | YES | MUL | XBRL taxonomy (xbrl rules) |
| `tag` | varchar(255) | YES |  | XBRL tag (xbrl rules) |
| `unit` | varchar(32) | YES |  | XBRL unit filter; NULL = any |
| `label_regex` | varchar(512) | YES |  | Row label pattern, MySQL REGEXP, case-insensitive (supplemental rules) |
| `exclude_regex` | varchar(512) | YES |  | Row labels matching this are excluded |
| `segment_regex` | varchar(512) | YES |  | Optional segment label filter |
| `col_role` | enum('value','change','share','any') | NO |  | Supplemental rules: which column role the rule reads (change = % change columns) |
| `table_kind_regex` | varchar(255) | YES |  | Supplemental rules: only tables whose kind matches, e.g. ^(same_store\|operating)$ |
| `priority` | smallint unsigned | NO |  | Lower wins when several rules match the same period |
| `multiplier` | decimal(18,8) | NO |  | Applied to the source value (e.g. 1000 for thousands) |
| `company_id` | int unsigned | YES | MUL | NULL = applies to all; set = company-specific override |
| `valid_from` | date | YES |  | Rule applies to periods ending on or after |
| `valid_to` | date | YES |  | Rule applies to periods ending on or before |
| `is_active` | tinyint(1) | NO |  | 0 = disabled (kept for audit) |
| `origin` | enum('seed','manual') | NO |  | seed rows are managed by the seed command; manual rows never touched by it |
| `notes` | varchar(512) | YES |  | Why this rule exists |
| `updated_at` | datetime(6) | NO |  | Last change (UTC) |

### ref_metric_sector

Table. Sector metric catalog: which metrics matter for which sector. Seeded from reference/data/metric_sectors.csv.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `metric_id` | smallint unsigned | NO | PRI | Metric |
| `sector_code` | varchar(32) | NO | PRI | Sector, or a group (equity, mortgage, all) covering its children |
| `relevance` | enum('core','common','optional') | NO |  | core: every company in the sector reports it; common: most do; optional: some do |
| `updated_at` | datetime(6) | NO |  | Last change (UTC) |

### ref_parse_override

Table. Per-company supplemental parsing rules. Rules are data, so fixing one REIT never changes code.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `override_id` | int unsigned | NO | PRI | Surrogate key |
| `override_key` | varchar(191) | NO | UNI | Stable key from the seed file (or manual:<n>) so edits upsert cleanly |
| `company_id` | int unsigned | NO | MUL | Company whose documents the rule applies to |
| `action` | enum('skip','force_kind','scale','min_confidence') | NO |  | skip a table; force its kind/orientation; set its scale; change the quarantine bar |
| `title_regex` | varchar(255) | YES |  | Applies to tables whose title matches (case-insensitive); NULL = all tables |
| `page_from` | int unsigned | YES |  | First page it applies to |
| `page_to` | int unsigned | YES |  | Last page it applies to |
| `params` | json | YES |  | Action parameters, e.g. {"kind": "tenant", "orientation": "records"} or {"money": 1000} |
| `valid_from` | date | YES |  | Applies to documents for periods ending on or after |
| `valid_to` | date | YES |  | Applies to documents for periods ending on or before |
| `is_active` | tinyint(1) | NO |  | 0 = disabled (kept for audit) |
| `origin` | enum('seed','manual') | NO |  | seed rows are managed by the seed command; manual rows never touched by it |
| `notes` | varchar(512) | YES |  | Why the rule exists |
| `updated_at` | datetime(6) | NO |  | Last change (UTC) |

### ref_property_source

Table. Source map: where each REIT lists its properties. Seeded from reference/data/property_sources.csv.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `source_row_id` | int unsigned | NO | PRI | Surrogate key |
| `company_id` | int unsigned | NO | MUL | Company |
| `source_kind` | enum('sec_schedule_iii','supplemental','corporate_website','public_dataset','manual') | NO |  | Kind of source |
| `lists_properties` | enum('yes','partial','no','unknown') | NO |  | yes: one row per property; partial: some properties or segments only; no: not at property level |
| `status` | enum('active','candidate','not_available','blocked','unknown') | NO |  | active: a loader runs it; candidate: found, no loader yet; not_available: source does not list properties; blocked: site refuses us |
| `list_url` | varchar(2048) | YES |  | Page or file that lists the properties (website and dataset sources) |
| `list_url_hash` | char(64) (generated) | YES |  | Hash of list_url, for uniqueness |
| `list_format` | enum('xbrl','html_table','html_cards','json','csv','xlsx','pdf','map_only','unknown') | NO |  | How the list is published (html_table, json, csv and xlsx have a loader) |
| `records_path` | varchar(255) | YES |  | JSON: dotted path to the list; HTML: table index (default the largest); XLSX: sheet name |
| `field_map` | varchar(1024) | YES |  | JSON object: record field to source column; empty = guess from headers |
| `detail` | varchar(32) | YES |  | Schedule III detail level: individual, by_market, mixed, unknown |
| `approx_property_count` | int unsigned | YES |  | Property count the company states, for coverage checks |
| `count_unit` | varchar(32) | YES |  | Unit of approx_property_count (properties, communities, hotels, towers...) |
| `checked_date` | date | YES |  | When the source map row was last researched |
| `notes` | varchar(1024) | YES |  | Research notes |
| `profile_source` | varchar(255) | YES |  | Seed file and hash this row came from |
| `updated_at` | datetime(6) | NO |  | Last change (UTC) |

### ref_property_type

Table. Property types and their sectors. Seeded from reference/data/property_types.csv.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `type_code` | varchar(32) | NO | PRI | Stable type code, e.g. apartment, senior_housing, net_lease_retail |
| `sector` | varchar(32) | NO |  | Sector the type rolls up to, e.g. residential, healthcare, retail |
| `name` | varchar(64) | NO |  | Display name |
| `default_unit_kind` | enum('sf','units','beds','keys','sites','acres','towers','screens','mw','other') | YES |  | Usual size unit for the type (sf, units, beds, keys, sites, acres...) |
| `description` | varchar(512) | YES |  | What belongs in the type |
| `updated_at` | datetime(6) | NO |  | Last change (UTC) |

### ref_property_type_map

Table. Rules mapping reported property type text to ref_property_type. Seeded from reference/data/property_type_map.csv.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `rule_id` | smallint unsigned | NO | PRI | Surrogate key |
| `rule_key` | varchar(64) | NO | UNI | Stable key from the seed file |
| `pattern` | varchar(255) | NO |  | Case-insensitive regular expression on the reported type, name or sector text |
| `type_code` | varchar(32) | NO | MUL | Type assigned when the pattern matches |
| `priority` | smallint | NO |  | Lower runs first; first match wins |
| `applies_to` | enum('type','name','company_sector') | NO |  | Which text the pattern is tested against |
| `notes` | varchar(255) | YES |  | Why the rule exists |

### ref_sector

Table. Property sectors. Seeded from reference/data/sectors.csv.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `sector_code` | varchar(32) | NO | PRI | Stable code, e.g. apartments, net_lease, senior_housing |
| `name` | varchar(96) | NO |  | Display name |
| `parent_code` | varchar(32) | YES |  | Broader group: equity or mortgage; all for the two groups |
| `description` | varchar(512) | YES |  | What the sector covers |
| `sort_order` | smallint unsigned | NO |  | Display order |
| `updated_at` | datetime(6) | NO |  | Last change (UTC) |

### ref_web_host

Table. robots.txt state per web host, refreshed by the IR fetcher (default every 24 hours).

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `host` | varchar(255) | NO | PRI | Lower-case host name |
| `robots_status` | smallint | YES |  | HTTP status of the last robots.txt fetch; NULL when unreachable |
| `robots_policy` | enum('parsed','allow_all','disallow_all') | NO |  | RFC 9309: 2xx parsed, 4xx allow all, 5xx or unreachable disallow all |
| `robots_sha256` | char(64) | YES | MUL | Raw object holding the robots.txt body |
| `crawl_delay_s` | decimal(8,2) | YES |  | Crawl-delay for our user agent, if any |
| `robots_fetched_at` | datetime(6) | NO |  | UTC time robots.txt was last fetched |

## stg_* Staging: parsed source records, one row per source fact, re-derivable from raw

- [`stg_doc_line_item`](#stg_doc_line_item): Individual values extracted from document tables, with page and cell position.
- [`stg_doc_parse`](#stg_doc_parse): Every parse of every document. Old parses are kept, so parser upgrades can be compared and rolled back.
- [`stg_doc_record`](#stg_doc_record): Rows of property, tenant, development, debt and lease-expiration tables in documents, as typed records.
- [`stg_doc_table`](#stg_doc_table): Tables found in parsed documents.
- [`stg_document`](#stg_document): Every supplemental, earnings release and other document from every source, one registry.
- [`stg_ir_link`](#stg_ir_link): Every document link seen on a company IR site. New supplemental links are downloaded.
- [`stg_property_record`](#stg_property_record): Every property row any source gave us: one per property per source per as-of date, faithful to the source, with lineage.
- [`stg_sec_filing`](#stg_sec_filing): Every SEC filing of every tracked company, from data.sec.gov submissions.
- [`stg_sec_filing_document`](#stg_sec_filing_document): Documents listed on EDGAR filing index pages (earnings 8-Ks, 10-Ks, 10-Qs).
- [`stg_xbrl_concept`](#stg_xbrl_concept): XBRL concepts seen in companyfacts.
- [`stg_xbrl_fact`](#stg_xbrl_fact): One row per value per reporting filing. Restatements are new rows, never updates.

### stg_doc_line_item

Table. Individual values extracted from document tables, with page and cell position.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `item_id` | bigint unsigned | NO | PRI | Surrogate key |
| `parse_id` | bigint unsigned | NO | MUL | Parse that produced it |
| `table_id` | bigint unsigned | YES |  | Table it sits in |
| `doc_id` | bigint unsigned | NO | MUL | Document |
| `page_no` | int unsigned | NO |  | Page number, 1-based |
| `row_index` | smallint unsigned | YES |  | Row in the table, 1-based |
| `col_index` | smallint unsigned | YES |  | Column in the table, 1-based |
| `row_label` | varchar(512) | NO |  | Row label as printed |
| `col_label` | varchar(255) | YES |  | Column header as printed |
| `metric_label` | varchar(512) | YES |  | Label naming the metric: row label (series tables) or column header (segment tables); rules match this |
| `raw_text` | varchar(255) | YES |  | Cell text as printed, e.g. (1,234) |
| `value_numeric` | decimal(28,6) | YES |  | Parsed number after sign and scale |
| `scale_hint` | varchar(32) | YES |  | Scale stated on the page, e.g. in thousands |
| `unit_hint` | varchar(32) | YES |  | Unit stated, e.g. $, %, sf |
| `period_start` | date | YES |  | Period start the column refers to |
| `period_end` | date | YES |  | Period end the column refers to |
| `period_type` | enum('Q','YTD','FY','TTM','INSTANT') | YES |  | Period type read from the header |
| `segment_label` | varchar(255) | YES |  | Segment, region or property type the row refers to |
| `group_label` | varchar(255) | YES |  | Sub-heading the row sits under |
| `col_role` | enum('value','change','share') | NO |  | value, change (e.g. % change) or share (e.g. % of total) column |
| `bbox` | json | YES |  | Cell position on the page [x0, top, x1, bottom] in PDF points |
| `confidence` | decimal(4,3) | YES |  | Parser confidence 0..1 |
| `quarantined` | tinyint(1) | NO |  | 1 = below the confidence bar or failed a hard check; not used by the warehouse |
| `quarantine_reason` | varchar(255) | YES |  | Why it was quarantined (or kept despite a warning) |

### stg_doc_parse

Table. Every parse of every document. Old parses are kept, so parser upgrades can be compared and rolled back.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `parse_id` | bigint unsigned | NO | PRI | Surrogate key of one parse attempt |
| `doc_id` | bigint unsigned | NO | MUL | Document parsed |
| `sha256` | char(64) | NO |  | Exact bytes parsed |
| `parser_name` | varchar(64) | NO |  | Parser used |
| `parser_version` | varchar(16) | NO |  | Parser version |
| `config_hash` | char(64) | YES |  | Hash of the overrides and settings the parse ran with (with parser_version, reproduces the parse) |
| `run_id` | bigint unsigned | YES |  | Job run |
| `status` | enum('running','succeeded','failed') | NO |  | Outcome |
| `n_tables` | int unsigned | NO |  | Tables extracted |
| `n_items` | int unsigned | NO |  | Line items extracted |
| `n_records` | int unsigned | NO |  | Records extracted |
| `n_quarantined` | int unsigned | NO |  | Items and records below the confidence bar |
| `started_at` | datetime(6) | NO |  | UTC start |
| `finished_at` | datetime(6) | YES |  | UTC end |
| `error_message` | text | YES |  | Error, when failed |

### stg_doc_record

Table. Rows of property, tenant, development, debt and lease-expiration tables in documents, as typed records.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `record_id` | bigint unsigned | NO | PRI | Surrogate key |
| `parse_id` | bigint unsigned | NO | MUL | Parse that produced it |
| `table_id` | bigint unsigned | YES |  | Table it sits in |
| `doc_id` | bigint unsigned | NO | MUL | Document |
| `page_no` | int unsigned | NO |  | Page number, 1-based |
| `row_index` | smallint unsigned | YES |  | Row in the table (column for wide maturity ladders), 1-based |
| `record_kind` | enum('property','tenant','development','debt_maturity','debt_detail','lease_expiration') | NO |  | What one row describes |
| `label` | varchar(512) | NO |  | Row label as printed, footnote markers removed (property, tenant, project, year) |
| `fields` | json | NO |  | Typed fields read from the row (names in reference/data/record_fields.csv); unmapped columns under extra |
| `as_of_date` | date | YES |  | Date the table describes |
| `bbox` | json | YES |  | Row position on the page [x0, top, x1, bottom] in PDF points |
| `confidence` | decimal(4,3) | YES |  | Parser confidence 0..1 |
| `quarantined` | tinyint(1) | NO |  | 1 = below the confidence bar or failed a hard check; not used by the warehouse |
| `quarantine_reason` | varchar(255) | YES |  | Why it was quarantined (or kept despite a warning) |

### stg_doc_table

Table. Tables found in parsed documents.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `table_id` | bigint unsigned | NO | PRI | Surrogate key |
| `parse_id` | bigint unsigned | NO | MUL | Parse that produced it |
| `doc_id` | bigint unsigned | NO |  | Document |
| `page_no` | int unsigned | NO |  | Page number, 1-based |
| `table_index` | smallint unsigned | NO |  | Order of the table on the page, 1-based |
| `title` | varchar(512) | YES |  | Table heading as printed |
| `section` | varchar(255) | YES |  | Section of the document, e.g. Debt Summary |
| `table_kind` | varchar(32) | YES |  | Kind from reference/data/table_kinds.csv, e.g. ffo, same_store, debt_maturity |
| `orientation` | enum('series','segments','records') | YES |  | series: periods across; segments: metrics across, segments down; records: one row per thing |
| `kind_score` | decimal(4,3) | YES |  | How well the title and labels matched the kind, 0..1 |
| `scale_text` | varchar(128) | YES |  | Scale note as printed, e.g. dollars in thousands |
| `scale_factor` | decimal(20,0) | YES |  | Multiplier applied to money values (1000 for thousands) |
| `scale_source` | enum('table','page','document','override','none') | YES |  | Where the scale came from: the table, its page, a document-wide statement, an override, or none |
| `headers` | json | YES |  | Column headers as read |
| `n_rows` | smallint unsigned | YES |  | Rows read |
| `n_cols` | smallint unsigned | YES |  | Value columns read |
| `total_check` | enum('pass','fail','mixed') | YES |  | Total rows equal the rows above them: pass, fail, mixed; NULL = no totals |
| `as_of_date` | date | YES |  | Date the table describes |
| `bbox` | json | YES |  | Position on the page [x0, y0, x1, y1] in PDF points |

### stg_document

Table. Every supplemental, earnings release and other document from every source, one registry.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `doc_id` | bigint unsigned | NO | PRI | Surrogate key |
| `company_id` | int unsigned | YES | MUL | Company; NULL until identified (archive imports) |
| `doc_kind` | enum('supplemental','earnings_release','presentation','exhibit_other','unknown') | NO |  | What the document is |
| `kind_source` | varchar(32) | YES |  | What set doc_kind: link rule, feed, archive, sec |
| `source` | enum('sec_8k_exhibit','ir_website','email','manual_archive') | NO | MUL | How we got it |
| `source_url` | varchar(2048) | NO |  | Where it came from (file:// URI for archive imports) |
| `source_url_hash` | char(64) (generated) | YES |  | Hash of source_url, for uniqueness |
| `accession_no` | char(20) | YES |  | SEC accession, for 8-K exhibits |
| `filing_id` | bigint unsigned | YES |  | SEC filing, for 8-K exhibits |
| `link_id` | bigint unsigned | YES |  | stg_ir_link the document was downloaded from |
| `original_filename` | varchar(255) | YES |  | File name as published |
| `title` | varchar(512) | YES |  | Title as published (link text, feed title or PDF title) |
| `sha256` | char(64) | YES | MUL | Raw object with the bytes; NULL until fetched |
| `bytes` | bigint unsigned | YES |  | Size |
| `mime_type` | varchar(128) | YES |  | MIME type |
| `period_end_date` | date | YES |  | Reporting period end the document covers |
| `period_source` | varchar(32) | YES |  | What set period_end_date: anchor, url, context, feed, pdf_text, manifest |
| `published_date` | date | YES |  | Date published (filing date for 8-Ks) = when the market could know it |
| `status` | enum('registered','fetched','parsed','needs_review','failed','duplicate','skipped') | NO | MUL | Lifecycle state |
| `duplicate_of_doc_id` | bigint unsigned | YES |  | Earlier document with identical bytes |
| `current_parse_id` | bigint unsigned | YES |  | Parse whose output the warehouse uses |
| `status_note` | varchar(1024) | YES |  | Reason for needs_review, failed, skipped |
| `registered_run_id` | bigint unsigned | YES |  | Run that registered it |
| `created_at` | datetime(6) | NO |  | Row creation (UTC) |
| `updated_at` | datetime(6) | NO |  | Last change (UTC) |

### stg_ir_link

Table. Every document link seen on a company IR site. New supplemental links are downloaded.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `link_id` | bigint unsigned | NO | PRI | Surrogate key |
| `company_id` | int unsigned | NO | MUL | Company |
| `source_id` | int unsigned | YES |  | ref_ir_source page the link was found on |
| `url` | varchar(2048) | NO |  | Absolute document URL (no fragment) |
| `url_hash` | char(64) (generated) | YES |  | Hash of url, for uniqueness |
| `anchor_text` | varchar(512) | YES |  | Link text as published |
| `context_text` | varchar(512) | YES |  | Nearby heading or row text, used for period inference |
| `doc_kind` | enum('supplemental','earnings_release','presentation','exhibit_other','unknown') | NO |  | Classification from link text and URL |
| `kind_reason` | varchar(64) | YES |  | Rule that set doc_kind |
| `fiscal_year` | smallint unsigned | YES |  | Fiscal year the document covers |
| `fiscal_quarter` | tinyint unsigned | YES |  | Fiscal quarter (1-4) the document covers |
| `period_end_date` | date | YES |  | Period end the document covers |
| `period_source` | varchar(32) | YES |  | Where the period came from: anchor, url, context, feed, pdf_text |
| `strategy` | varchar(16) | NO |  | Fetch strategy that found it |
| `status` | enum('new','downloaded','skipped','failed','robots_blocked') | NO | MUL | Download state |
| `status_note` | varchar(512) | YES |  | Why skipped or failed |
| `doc_id` | bigint unsigned | YES | MUL | Document registered from this link |
| `first_seen_at` | datetime(6) | NO |  | UTC time first seen = discovery time |
| `last_seen_at` | datetime(6) | NO |  | UTC time last seen on the page |
| `first_run_id` | bigint unsigned | YES |  | Run that first saw it |

### stg_property_record

Table. Every property row any source gave us: one per property per source per as-of date, faithful to the source, with lineage.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `record_id` | bigint unsigned | NO | PRI | Surrogate key; supplemental property metrics key to this |
| `record_key` | char(64) | NO | UNI | Hash of company, source, source locator, as-of date and row identity; reloads are idempotent on it |
| `company_id` | int unsigned | NO | MUL | Company whose list this row came from |
| `source_kind` | enum('sec_schedule_iii','supplemental','corporate_website','public_dataset','manual') | NO |  | Kind of source |
| `source_row_id` | int unsigned | YES |  | ref_property_source row, when the source is in the source map |
| `source_doc_id` | bigint unsigned | YES | MUL | stg_document row (supplementals) |
| `source_parse_id` | bigint unsigned | YES |  | stg_doc_parse row (supplementals) |
| `source_table_id` | bigint unsigned | YES |  | stg_doc_table row (supplementals) |
| `source_page_no` | int unsigned | YES |  | Page in the source document |
| `source_row_index` | int unsigned | YES |  | Row position in the source table or list, 1-based |
| `source_sha256` | char(64) | YES |  | Raw object the row was read from |
| `source_url` | varchar(2048) | YES |  | URL the row was read from |
| `accession_no` | char(20) | YES |  | SEC filing (Schedule III) |
| `as_of_date` | date | NO |  | Date the list describes (balance sheet date, page date for websites) |
| `known_from` | date | NO |  | Knowledge time: when the market could know it (filing or publication date, or fetch date) |
| `source_property_ref` | varchar(255) | YES |  | The source's own identifier for the row (XBRL member, website id, property code) |
| `name` | varchar(255) | YES |  | Property name as reported |
| `address_line` | varchar(255) | YES |  | Street address as reported |
| `city` | varchar(128) | YES |  | City |
| `state` | varchar(64) | YES |  | State or province (2-letter for the US when known) |
| `postal_code` | varchar(16) | YES |  | Postal code |
| `country` | char(2) | YES |  | ISO country code |
| `market` | varchar(128) | YES |  | Market, MSA or region as reported |
| `raw_property_type` | varchar(128) | YES |  | Property type text as reported |
| `type_code` | varchar(32) | YES | MUL | ref_property_type code from ref_property_type_map rules |
| `rentable_sf` | decimal(16,2) | YES |  | Rentable square feet |
| `unit_count` | decimal(14,2) | YES |  | Size in unit_kind (apartment units, beds, keys, sites, acres...) |
| `unit_kind` | enum('sf','units','beds','keys','sites','acres','towers','screens','mw','other') | YES |  | Unit of unit_count |
| `land_acres` | decimal(14,3) | YES |  | Land area in acres |
| `year_built` | smallint unsigned | YES |  | Year built (first year when a range is reported) |
| `year_renovated` | smallint unsigned | YES |  | Latest renovation year |
| `acquired_date` | date | YES |  | Acquisition date as reported (first day of the year when only a year is given) |
| `acquired_text` | varchar(64) | YES |  | Acquisition date text as printed, e.g. 2004/2016 |
| `disposed_date` | date | YES |  | Disposition date as reported |
| `ownership_pct` | decimal(7,4) | YES |  | Company share of the property, percent |
| `interest_type` | enum('fee','ground_lease','leasehold','joint_venture','mortgage','unknown') | YES |  | Fee, ground lease, leasehold, joint venture, mortgage |
| `occupancy_pct` | decimal(7,4) | YES |  | Occupancy percent as reported with the list |
| `encumbrances` | decimal(20,2) | YES |  | Schedule III: encumbrances, USD |
| `initial_cost_land` | decimal(20,2) | YES |  | Schedule III: initial cost of land, USD |
| `initial_cost_bldg` | decimal(20,2) | YES |  | Schedule III: initial cost of buildings and improvements, USD |
| `costs_capitalized` | decimal(20,2) | YES |  | Schedule III: costs capitalized after acquisition, USD |
| `gross_land` | decimal(20,2) | YES |  | Schedule III: gross amount carried, land, USD |
| `gross_bldg` | decimal(20,2) | YES |  | Schedule III: gross amount carried, buildings and improvements, USD |
| `gross_total` | decimal(20,2) | YES |  | Schedule III: gross amount carried, total, USD |
| `accum_depreciation` | decimal(20,2) | YES |  | Schedule III: accumulated depreciation, USD |
| `is_aggregate` | tinyint(1) | NO |  | 1 when the row stands for several properties (market, type or other totals); never becomes a property |
| `aggregate_count` | int unsigned | YES |  | Properties the aggregate row stands for, when stated |
| `latitude` | decimal(9,6) | YES |  | Latitude as reported by the source |
| `longitude` | decimal(9,6) | YES |  | Longitude as reported by the source |
| `attributes` | json | YES |  | Sector-specific attributes as reported (e.g. tenant, brand, operator, care mix, MW) |
| `norm_name` | varchar(255) | YES |  | Normalized name used for matching |
| `norm_address` | varchar(255) | YES |  | Normalized street address used for matching |
| `address_key` | char(64) | YES | MUL | Hash of normalized street and ZIP (or city and state); NULL without a street address |
| `loader` | varchar(64) | NO |  | Loader or parser that produced the row |
| `loader_version` | varchar(16) | NO |  | Its version |
| `run_id` | bigint unsigned | YES |  | Job run that loaded it |
| `created_at` | datetime(6) | NO |  | System time inserted |
| `retired_at` | datetime(6) | YES |  | System time a newer load of the same source replaced it; NULL = live |
| `is_live` | tinyint (generated) | YES |  | 1 while live, NULL once retired |

### stg_sec_filing

Table. Every SEC filing of every tracked company, from data.sec.gov submissions.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `filing_id` | bigint unsigned | NO | PRI | Surrogate key |
| `company_id` | int unsigned | NO | MUL | Filer |
| `accession_no` | char(20) | NO | MUL | SEC accession number 0000000000-00-000000 |
| `form_type` | varchar(32) | NO | MUL | Form, e.g. 10-K, 10-Q, 8-K, 10-K/A |
| `filing_date` | date | NO |  | Date filed with SEC (when the market could know it) |
| `report_date` | date | YES |  | Period of report |
| `acceptance_datetime` | datetime | YES |  | EDGAR acceptance timestamp |
| `act` | varchar(16) | YES |  | Securities act |
| `file_number` | varchar(32) | YES |  | SEC file number |
| `film_number` | varchar(32) | YES |  | SEC film number |
| `items` | varchar(255) | YES |  | 8-K item numbers, e.g. 2.02,9.01 |
| `size_bytes` | bigint unsigned | YES |  | Filing size per SEC |
| `is_xbrl` | tinyint(1) | YES |  | Has XBRL |
| `is_inline_xbrl` | tinyint(1) | YES |  | Has inline XBRL |
| `primary_document` | varchar(255) | YES |  | Primary document file name |
| `primary_doc_description` | varchar(255) | YES |  | Primary document description |
| `index_url` | varchar(512) | NO |  | EDGAR filing index page |
| `primary_doc_url` | varchar(512) | YES |  | URL of the primary document |
| `is_amendment` | tinyint(1) (generated) | YES |  | 1 for amended filings |
| `src_sha256` | char(64) | NO |  | Raw submissions JSON this row was parsed from |
| `parser_version` | varchar(16) | NO |  | Parser version that produced the row |
| `first_seen_run_id` | bigint unsigned | YES |  | Run that first loaded it |
| `docs_indexed_at` | datetime(6) | YES |  | UTC time its index page was processed; NULL = not yet |
| `docs_index_sha256` | char(64) | YES |  | Raw object of the index page |
| `created_at` | datetime(6) | NO |  | Row creation (UTC) |

### stg_sec_filing_document

Table. Documents listed on EDGAR filing index pages (earnings 8-Ks, 10-Ks, 10-Qs).

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `filing_id` | bigint unsigned | NO | PRI | Filing |
| `document_name` | varchar(255) | NO | PRI | File name inside the filing |
| `seq` | smallint unsigned | YES |  | Sequence number on the index page |
| `doc_type` | varchar(64) | YES | MUL | Document type, e.g. 10-Q, EX-99.1, EX-99.2 |
| `description` | varchar(512) | YES |  | Description on the index page |
| `url` | varchar(512) | NO |  | Document URL |
| `size_bytes` | bigint unsigned | YES |  | Size per index page |
| `src_sha256` | char(64) | NO |  | Raw index page this row was parsed from |

### stg_xbrl_concept

Table. XBRL concepts seen in companyfacts.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `concept_id` | int unsigned | NO | PRI | Surrogate key |
| `taxonomy` | varchar(32) | NO | MUL | us-gaap, dei, srt, ... |
| `tag` | varchar(255) | NO |  | XBRL element name |
| `label` | varchar(512) | YES |  | Standard label |
| `description` | text | YES |  | Taxonomy documentation |

### stg_xbrl_fact

Table. One row per value per reporting filing. Restatements are new rows, never updates.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `fact_id` | bigint unsigned | NO | PRI | Surrogate key |
| `company_id` | int unsigned | NO | MUL | Reporting company |
| `concept_id` | int unsigned | NO | MUL | Concept |
| `unit` | varchar(32) | NO |  | Unit, e.g. USD, USD/shares, shares |
| `period_start` | date | YES |  | Start for duration facts; NULL for instants |
| `period_end` | date | NO |  | End date or instant date |
| `period_start_key` | date (generated) | YES |  | Non-null period_start for keys |
| `duration_days` | smallint (generated) | YES |  | Length of a duration fact in days |
| `value` | decimal(28,6) | NO |  | Reported value |
| `accession_no` | char(20) | NO |  | Filing that reported this value (the same period recurs in later filings) |
| `fy` | smallint | YES |  | Fiscal year of the reporting filing (SEC field, not the period) |
| `fp` | varchar(4) | YES |  | Fiscal period of the reporting filing (SEC field) |
| `form` | varchar(32) | YES |  | Form of the reporting filing |
| `filed` | date | NO |  | Filing date of the reporting filing = when the value became known |
| `frame` | varchar(16) | YES |  | SEC frame, e.g. CY2024Q1 |
| `src_sha256` | char(64) | NO |  | Raw companyfacts JSON this row was first parsed from |
| `first_seen_run_id` | bigint unsigned | YES |  | Run that first loaded it |

## wh_* Warehouse: curated, bitemporal, lineage on every value

- [`wh_debt_instrument`](#wh_debt_instrument): Individual loans and notes from supplemental debt schedules (input to debt mark-to-market in NAV).
- [`wh_debt_maturity`](#wh_debt_maturity): Debt maturity ladders from supplementals.
- [`wh_development_project`](#wh_development_project): Development and redevelopment pipelines from supplementals.
- [`wh_lease_expiration`](#wh_lease_expiration): Lease expiration schedules from supplementals.
- [`wh_metric_latest`](#wh_metric_latest) (view): VIEW
- [`wh_metric_value`](#wh_metric_value): Curated metric values, bitemporal: valid time (period), knowledge time (known_from/to) and system time (recorded/retired). Rows are retired, never deleted.
- [`wh_property`](#wh_property): Property master: one row per real property, stable ids, canonical attributes chosen by source priority.
- [`wh_property_alias`](#wh_property_alias): Every name, address and source reference seen for a property, so later records match it.
- [`wh_property_metric`](#wh_property_metric): Per-property figures from supplementals (size, occupancy, rent, NOI, RevPAR ...), bitemporal, with lineage to page and row.
- [`wh_property_ownership`](#wh_property_ownership): Ownership spells: which REIT owned which property when, with system time like wh_metric_value.
- [`wh_property_snapshot`](#wh_property_snapshot): Property figures per reporting date, with lineage to page and cell.
- [`wh_property_xref`](#wh_property_xref): Record to property links with method, score and review status. Manual links survive rebuilds.
- [`wh_tenant_exposure`](#wh_tenant_exposure): Top tenant, customer and operator concentration from supplementals.

### wh_debt_instrument

Table. Individual loans and notes from supplemental debt schedules (input to debt mark-to-market in NAV).

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `instrument_id` | bigint unsigned | NO | PRI | Surrogate key |
| `company_id` | int unsigned | NO | MUL | Borrower |
| `as_of_date` | date | NO |  | Date of the schedule |
| `instrument` | varchar(255) | NO |  | Loan or note as printed, e.g. 3.600% notes due 2026 |
| `segment` | varchar(128) | NO |  | Sub-heading, e.g. Unsecured, Mortgages |
| `debt_type` | varchar(64) | YES |  | Type column as printed |
| `principal` | decimal(20,2) | YES |  | Principal outstanding, USD |
| `rate` | decimal(9,4) | YES |  | Stated interest rate, percent |
| `effective_rate` | decimal(9,4) | YES |  | Effective interest rate, percent |
| `maturity_text` | varchar(64) | YES |  | Maturity as printed |
| `maturity_year` | smallint | YES |  | Maturity year read from the text |
| `is_total` | tinyint(1) | NO |  | 1 for a total row |
| `known_from` | date | NO |  | Knowledge time: publication date of the source document |
| `known_to` | date | YES |  | Knowledge time: date a later document gave a different value; NULL = still the latest |
| `version` | smallint unsigned | NO |  | 1 = first reported, 2+ = later revisions of the same row |
| `confidence` | decimal(4,3) | YES |  | Parser confidence 0..1 of the source record |
| `source_doc_id` | bigint unsigned | NO |  | stg_document row |
| `source_sha256` | char(64) | YES |  | Raw object (exact bytes) the value was read from |
| `source_page_no` | int unsigned | YES |  | Page in the source document |
| `source_table_id` | bigint unsigned | YES |  | stg_doc_table row |
| `source_record_id` | bigint unsigned | YES |  | stg_doc_record row |
| `row_hash` | char(64) | NO | MUL | Hash of key, knowledge time, values and lineage; equal hash = identical row |
| `recorded_at` | datetime(6) | NO |  | System time: when this row entered the warehouse |
| `recorded_run_id` | bigint unsigned | YES |  | Build run that inserted it |
| `retired_at` | datetime(6) | YES |  | System time: when a rebuild replaced it; NULL = live |
| `retired_run_id` | bigint unsigned | YES |  | Build run that retired it |
| `is_live` | tinyint (generated) | YES |  | 1 while live, NULL once retired |

### wh_debt_maturity

Table. Debt maturity ladders from supplementals.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `maturity_id` | bigint unsigned | NO | PRI | Surrogate key |
| `company_id` | int unsigned | NO | MUL | Company |
| `as_of_date` | date | NO |  | Balance sheet date of the schedule |
| `maturity_year` | smallint | YES |  | Year the debt matures; NULL for Thereafter and Total buckets |
| `bucket_label` | varchar(64) | YES |  | Bucket as printed: a year, Thereafter, Total |
| `debt_type` | varchar(64) | NO |  | Secured, unsecured, line of credit, Total |
| `amount` | decimal(20,2) | NO |  | Principal maturing, USD |
| `wtd_avg_rate` | decimal(7,4) | YES |  | Weighted average rate of that bucket, percent |
| `pct_of_total` | decimal(9,4) | YES |  | Percent of total debt maturing in the bucket |
| `is_total` | tinyint(1) | NO |  | 1 for the total row |
| `known_from` | date | NO |  | Knowledge time: publication date |
| `known_to` | date | YES |  | Knowledge time: date a later document gave a different value; NULL = still the latest |
| `version` | smallint unsigned | NO |  | 1 = first reported, 2+ = later revisions |
| `confidence` | decimal(4,3) | YES |  | Parser confidence 0..1 of the source record |
| `source_doc_id` | bigint unsigned | NO |  | Source document |
| `source_sha256` | char(64) | YES |  | Raw object the value was read from |
| `source_page_no` | int unsigned | YES |  | Source page |
| `source_table_id` | bigint unsigned | YES |  | stg_doc_table row |
| `source_record_id` | bigint unsigned | YES |  | stg_doc_record row |
| `row_hash` | char(64) | YES | MUL | Hash of key, knowledge time, values and lineage |
| `recorded_at` | datetime(6) | NO |  | System time inserted |
| `recorded_run_id` | bigint unsigned | YES |  | Build run that inserted it |
| `retired_at` | datetime(6) | YES |  | System time replaced; NULL = live |
| `retired_run_id` | bigint unsigned | YES |  | Build run that retired it |
| `is_live` | tinyint (generated) | YES |  | 1 while live, NULL once retired |

### wh_development_project

Table. Development and redevelopment pipelines from supplementals.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `dev_project_id` | bigint unsigned | NO | PRI | Surrogate key |
| `company_id` | int unsigned | NO | MUL | Company |
| `as_of_date` | date | NO |  | Date of the schedule |
| `project_name` | varchar(255) | NO |  | Project, region or bucket as printed |
| `segment` | varchar(128) | NO |  | Sub-heading the row sat under (e.g. Under construction, Completed), or Total |
| `market` | varchar(128) | YES |  | Market as printed |
| `property_type` | varchar(64) | YES |  | Property type as printed |
| `units` | int | YES |  | Units, homes, beds, keys or MW |
| `square_feet` | decimal(16,2) | YES |  | Square feet |
| `total_cost` | decimal(20,2) | YES |  | Estimated total cost or investment, USD |
| `cost_to_date` | decimal(20,2) | YES |  | Cost incurred to date, USD |
| `remaining_cost` | decimal(20,2) | YES |  | Cost remaining to fund, USD |
| `pct_leased` | decimal(9,4) | YES |  | Percent leased or pre-leased |
| `stabilized_yield` | decimal(9,4) | YES |  | Expected stabilized yield, percent |
| `ownership_pct` | decimal(9,4) | YES |  | Company ownership share, percent |
| `completion_text` | varchar(64) | YES |  | Expected completion or stabilization as printed |
| `is_total` | tinyint(1) | NO |  | 1 for a total row |
| `known_from` | date | NO |  | Knowledge time: publication date of the source document |
| `known_to` | date | YES |  | Knowledge time: date a later document gave a different value; NULL = still the latest |
| `version` | smallint unsigned | NO |  | 1 = first reported, 2+ = later revisions of the same row |
| `confidence` | decimal(4,3) | YES |  | Parser confidence 0..1 of the source record |
| `source_doc_id` | bigint unsigned | NO |  | stg_document row |
| `source_sha256` | char(64) | YES |  | Raw object (exact bytes) the value was read from |
| `source_page_no` | int unsigned | YES |  | Page in the source document |
| `source_table_id` | bigint unsigned | YES |  | stg_doc_table row |
| `source_record_id` | bigint unsigned | YES |  | stg_doc_record row |
| `row_hash` | char(64) | NO | MUL | Hash of key, knowledge time, values and lineage; equal hash = identical row |
| `recorded_at` | datetime(6) | NO |  | System time: when this row entered the warehouse |
| `recorded_run_id` | bigint unsigned | YES |  | Build run that inserted it |
| `retired_at` | datetime(6) | YES |  | System time: when a rebuild replaced it; NULL = live |
| `retired_run_id` | bigint unsigned | YES |  | Build run that retired it |
| `is_live` | tinyint (generated) | YES |  | 1 while live, NULL once retired |

### wh_lease_expiration

Table. Lease expiration schedules from supplementals.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `lease_exp_id` | bigint unsigned | NO | PRI | Surrogate key |
| `company_id` | int unsigned | NO | MUL | Company |
| `as_of_date` | date | NO |  | Date of the schedule |
| `bucket_label` | varchar(64) | NO |  | Bucket as printed: a year, Thereafter, Month-to-month, Vacant, Total |
| `expiry_year` | smallint | YES |  | Year, when the bucket is one year |
| `segment` | varchar(128) | NO |  | Sub-heading the row sat under, or Total |
| `n_leases` | int | YES |  | Leases expiring |
| `n_properties` | int | YES |  | Properties with leases expiring |
| `square_feet` | decimal(16,2) | YES |  | Square feet expiring |
| `annualized_rent` | decimal(20,2) | YES |  | Annualized rent expiring, USD |
| `rent_pct` | decimal(9,4) | YES |  | Percent of total annualized rent |
| `rent_psf` | decimal(12,4) | YES |  | Annualized rent per square foot, USD |
| `is_total` | tinyint(1) | NO |  | 1 for the total row |
| `known_from` | date | NO |  | Knowledge time: publication date of the source document |
| `known_to` | date | YES |  | Knowledge time: date a later document gave a different value; NULL = still the latest |
| `version` | smallint unsigned | NO |  | 1 = first reported, 2+ = later revisions of the same row |
| `confidence` | decimal(4,3) | YES |  | Parser confidence 0..1 of the source record |
| `source_doc_id` | bigint unsigned | NO |  | stg_document row |
| `source_sha256` | char(64) | YES |  | Raw object (exact bytes) the value was read from |
| `source_page_no` | int unsigned | YES |  | Page in the source document |
| `source_table_id` | bigint unsigned | YES |  | stg_doc_table row |
| `source_record_id` | bigint unsigned | YES |  | stg_doc_record row |
| `row_hash` | char(64) | NO | MUL | Hash of key, knowledge time, values and lineage; equal hash = identical row |
| `recorded_at` | datetime(6) | NO |  | System time: when this row entered the warehouse |
| `recorded_run_id` | bigint unsigned | YES |  | Build run that inserted it |
| `retired_at` | datetime(6) | YES |  | System time: when a rebuild replaced it; NULL = live |
| `retired_run_id` | bigint unsigned | YES |  | Build run that retired it |
| `is_live` | tinyint (generated) | YES |  | 1 while live, NULL once retired |

### wh_metric_latest

View.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `value_id` | bigint unsigned | NO |  | Surrogate key |
| `company_id` | int unsigned | NO |  | Company |
| `metric_id` | smallint unsigned | NO |  | Metric |
| `period_type` | enum('Q','YTD','FY','TTM','INSTANT') | NO |  | Q quarter, YTD year to date, FY fiscal year, INSTANT balance |
| `period_start` | date | YES |  | Business period start (valid time); NULL for INSTANT |
| `period_end` | date | NO |  | Business period end or balance date (valid time) |
| `period_start_key` | date | YES |  | Non-null period_start for keys |
| `fiscal_year` | smallint | NO |  | Fiscal year (named for the calendar year it ends in) |
| `fiscal_quarter` | tinyint | YES |  | 1..4 for Q, YTD and INSTANT; NULL for FY |
| `segment` | varchar(128) | NO |  | Total or a segment label |
| `value` | decimal(28,6) | NO |  | Value in unit |
| `unit` | varchar(32) | NO |  | Unit, e.g. USD, USD/shares, pct |
| `source_type` | enum('xbrl','derived','supplemental','manual') | NO |  | xbrl: as tagged; derived: computed from xbrl (e.g. Q4 = FY - 9M); supplemental: parsed from a document; manual: analyst entry |
| `version` | smallint unsigned | NO |  | 1 = first reported, 2+ = restatements (knowledge time order) |
| `known_from` | date | NO |  | Knowledge time: date the value was published (filing or document date) |
| `known_to` | date | YES |  | Knowledge time: date a different value was published; NULL = still the latest |
| `source_accession` | char(20) | YES |  | SEC filing the value comes from |
| `source_fact_id` | bigint unsigned | YES |  | stg_xbrl_fact row (xbrl) |
| `source_doc_id` | bigint unsigned | YES |  | stg_document row (supplemental) |
| `source_item_id` | bigint unsigned | YES |  | stg_doc_line_item row (supplemental) |
| `source_page_no` | int unsigned | YES |  | Page in the source document |
| `source_table_id` | bigint unsigned | YES |  | stg_doc_table row |
| `source_sha256` | char(64) | YES |  | Raw object behind the value |
| `derivation` | varchar(255) | YES |  | How a derived value was computed, with the facts used |
| `row_hash` | char(64) | NO |  | Hash of key, knowledge time, value and lineage; equal hash = identical row |
| `recorded_at` | datetime(6) | NO |  | System time: when this row entered the warehouse |
| `recorded_run_id` | bigint unsigned | YES |  | Build run that inserted it |
| `retired_at` | datetime(6) | YES |  | System time: when a rebuild replaced it (mapping or parser change); NULL = live |
| `retired_run_id` | bigint unsigned | YES |  | Build run that retired it |
| `is_live` | tinyint | YES |  | 1 while live, NULL once retired |
| `is_preferred` | int | NO |  |  |

### wh_metric_value

Table. Curated metric values, bitemporal: valid time (period), knowledge time (known_from/to) and system time (recorded/retired). Rows are retired, never deleted.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `value_id` | bigint unsigned | NO | PRI | Surrogate key |
| `company_id` | int unsigned | NO | MUL | Company |
| `metric_id` | smallint unsigned | NO | MUL | Metric |
| `period_type` | enum('Q','YTD','FY','TTM','INSTANT') | NO |  | Q quarter, YTD year to date, FY fiscal year, INSTANT balance |
| `period_start` | date | YES |  | Business period start (valid time); NULL for INSTANT |
| `period_end` | date | NO |  | Business period end or balance date (valid time) |
| `period_start_key` | date (generated) | YES |  | Non-null period_start for keys |
| `fiscal_year` | smallint | NO |  | Fiscal year (named for the calendar year it ends in) |
| `fiscal_quarter` | tinyint | YES |  | 1..4 for Q, YTD and INSTANT; NULL for FY |
| `segment` | varchar(128) | NO |  | Total or a segment label |
| `value` | decimal(28,6) | NO |  | Value in unit |
| `unit` | varchar(32) | NO |  | Unit, e.g. USD, USD/shares, pct |
| `source_type` | enum('xbrl','derived','supplemental','manual') | NO |  | xbrl: as tagged; derived: computed from xbrl (e.g. Q4 = FY - 9M); supplemental: parsed from a document; manual: analyst entry |
| `version` | smallint unsigned | NO |  | 1 = first reported, 2+ = restatements (knowledge time order) |
| `known_from` | date | NO | MUL | Knowledge time: date the value was published (filing or document date) |
| `known_to` | date | YES |  | Knowledge time: date a different value was published; NULL = still the latest |
| `source_accession` | char(20) | YES |  | SEC filing the value comes from |
| `source_fact_id` | bigint unsigned | YES |  | stg_xbrl_fact row (xbrl) |
| `source_doc_id` | bigint unsigned | YES |  | stg_document row (supplemental) |
| `source_item_id` | bigint unsigned | YES |  | stg_doc_line_item row (supplemental) |
| `source_page_no` | int unsigned | YES |  | Page in the source document |
| `source_table_id` | bigint unsigned | YES |  | stg_doc_table row |
| `source_sha256` | char(64) | YES |  | Raw object behind the value |
| `derivation` | varchar(255) | YES |  | How a derived value was computed, with the facts used |
| `row_hash` | char(64) | NO | MUL | Hash of key, knowledge time, value and lineage; equal hash = identical row |
| `recorded_at` | datetime(6) | NO |  | System time: when this row entered the warehouse |
| `recorded_run_id` | bigint unsigned | YES |  | Build run that inserted it |
| `retired_at` | datetime(6) | YES |  | System time: when a rebuild replaced it (mapping or parser change); NULL = live |
| `retired_run_id` | bigint unsigned | YES |  | Build run that retired it |
| `is_live` | tinyint (generated) | YES |  | 1 while live, NULL once retired |

### wh_property

Table. Property master: one row per real property, stable ids, canonical attributes chosen by source priority.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `property_id` | bigint unsigned | NO | PRI | Stable property id: assigned once, never reused, survives sales between REITs |
| `status` | enum('active','inactive','merged') | NO |  | active: owned by a tracked REIT now; inactive: no current owner in the universe; merged: duplicate, see merged_into_property_id |
| `merged_into_property_id` | bigint unsigned | YES | MUL | Survivor when this row was found to duplicate another |
| `owner_company_id` | int unsigned | YES | MUL | Largest current owner (denormalized from wh_property_ownership) |
| `name` | varchar(255) | YES |  | Canonical name (highest-priority, newest source) |
| `address_line` | varchar(255) | YES |  | Canonical street address |
| `city` | varchar(128) | YES |  | City |
| `state` | varchar(64) | YES | MUL | State or province |
| `postal_code` | varchar(16) | YES |  | Postal code |
| `country` | char(2) | YES |  | ISO country code |
| `market` | varchar(128) | YES |  | Market or MSA as reported |
| `type_code` | varchar(32) | YES | MUL | Property type |
| `sector` | varchar(32) | YES |  | Sector of the type |
| `rentable_sf` | decimal(16,2) | YES |  | Latest rentable square feet |
| `unit_count` | decimal(14,2) | YES |  | Latest size in unit_kind |
| `unit_kind` | enum('sf','units','beds','keys','sites','acres','towers','screens','mw','other') | YES |  | Unit of unit_count |
| `land_acres` | decimal(14,3) | YES |  | Land area in acres |
| `year_built` | smallint unsigned | YES |  | Year built |
| `latitude` | decimal(9,6) | YES |  | Latitude (WGS84) |
| `longitude` | decimal(9,6) | YES |  | Longitude (WGS84) |
| `geo_precision` | enum('rooftop','street','zip','city','reported','none') | NO |  | Precision of the point |
| `geo_source` | varchar(32) | YES |  | Where the point came from: census, source, manual |
| `address_key` | char(64) | YES | MUL | Hash of the canonical normalized address |
| `first_seen` | date | YES |  | Earliest as-of date of any linked record |
| `last_seen` | date | YES |  | Latest as-of date of any linked record |
| `n_records` | int unsigned | NO |  | Live records linked to it |
| `n_source_kinds` | tinyint unsigned | NO |  | Distinct source kinds that list it (more = better confirmed) |
| `attr_hash` | char(64) | YES |  | Hash of the canonical attributes; a rebuild updates the row only when it changes |
| `created_run_id` | bigint unsigned | YES |  | Build run that created the id |
| `created_at` | datetime(6) | NO |  | Row creation (UTC) |
| `updated_at` | datetime(6) | NO |  | Last change (UTC) |

### wh_property_alias

Table. Every name, address and source reference seen for a property, so later records match it.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `property_id` | bigint unsigned | NO | PRI | Property |
| `alias_type` | enum('name','address','source_ref') | NO | PRI | What the alias is |
| `alias_norm` | varchar(255) | NO |  | Normalized alias used for matching |
| `alias_hash` | char(64) (generated) | NO | PRI | Hash of alias_norm, for the key |
| `alias_value` | varchar(512) | NO |  | Alias as first reported |
| `company_id` | int unsigned | YES |  | Company whose source used it |
| `source_kind` | enum('sec_schedule_iii','supplemental','corporate_website','public_dataset','manual') | NO | PRI | Source kind that used it |
| `first_seen` | date | NO |  | Earliest as-of date it was used |
| `last_seen` | date | NO |  | Latest as-of date it was used |

### wh_property_metric

Table. Per-property figures from supplementals (size, occupancy, rent, NOI, RevPAR ...), bitemporal, with lineage to page and row.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `property_metric_id` | bigint unsigned | NO | PRI | Surrogate key |
| `company_id` | int unsigned | NO | MUL | Reporting company |
| `property_record_id` | bigint unsigned | YES | MUL | stg_property_record row for this property in this document; the property master links it to a property_id through wh_property_xref |
| `property_key` | char(64) | NO |  | Hash of company-normalized name, city and state; the version key across documents |
| `property_name` | varchar(255) | NO |  | Property name as printed |
| `city` | varchar(128) | YES |  | City as printed |
| `state` | varchar(64) | YES |  | State as printed |
| `market` | varchar(128) | YES |  | Market or MSA as printed |
| `property_type` | varchar(64) | YES |  | Property type as printed |
| `metric_id` | smallint unsigned | NO | MUL | Metric (grain = property) |
| `as_of_date` | date | NO |  | Date or period end the figure describes (valid time) |
| `value` | decimal(28,6) | NO |  | Value in unit |
| `unit` | varchar(32) | NO |  | Unit, e.g. USD, sf, pct, count |
| `known_from` | date | NO |  | Knowledge time: publication date of the source document |
| `known_to` | date | YES |  | Knowledge time: date a later document gave a different value; NULL = still the latest |
| `version` | smallint unsigned | NO |  | 1 = first reported, 2+ = later revisions of the same row |
| `confidence` | decimal(4,3) | YES |  | Parser confidence 0..1 of the source record |
| `source_doc_id` | bigint unsigned | NO |  | stg_document row |
| `source_sha256` | char(64) | YES |  | Raw object (exact bytes) the value was read from |
| `source_page_no` | int unsigned | YES |  | Page in the source document |
| `source_table_id` | bigint unsigned | YES |  | stg_doc_table row |
| `source_record_id` | bigint unsigned | YES |  | stg_doc_record row |
| `row_hash` | char(64) | NO | MUL | Hash of key, knowledge time, values and lineage; equal hash = identical row |
| `recorded_at` | datetime(6) | NO |  | System time: when this row entered the warehouse |
| `recorded_run_id` | bigint unsigned | YES |  | Build run that inserted it |
| `retired_at` | datetime(6) | YES |  | System time: when a rebuild replaced it; NULL = live |
| `retired_run_id` | bigint unsigned | YES |  | Build run that retired it |
| `is_live` | tinyint (generated) | YES |  | 1 while live, NULL once retired |

### wh_property_ownership

Table. Ownership spells: which REIT owned which property when, with system time like wh_metric_value.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `ownership_id` | bigint unsigned | NO | PRI | Surrogate key |
| `property_id` | bigint unsigned | NO | MUL | Property |
| `company_id` | int unsigned | NO | MUL | Owner |
| `spell_no` | smallint unsigned | NO |  | 1 for the first period of ownership, 2+ if the company sold and bought it back |
| `ownership_pct` | decimal(7,4) | YES |  | Latest reported share, percent (NULL = not reported, usually 100) |
| `interest_type` | enum('fee','ground_lease','leasehold','joint_venture','mortgage','unknown') | YES |  | Latest reported interest type |
| `acquired_date` | date | YES |  | Start of ownership |
| `acquired_basis` | enum('reported','first_seen') | NO |  | reported by a source, or the first as-of date it was listed |
| `disposed_date` | date | YES |  | End of ownership; NULL = still owned |
| `disposed_basis` | enum('reported','not_listed') | YES |  | reported by a source, or the first complete list from the same source that no longer had it |
| `first_seen_as_of` | date | NO |  | Earliest as-of date the company listed it |
| `last_seen_as_of` | date | NO |  | Latest as-of date the company listed it |
| `known_from` | date | NO |  | Knowledge time: first publication of the spell |
| `last_record_id` | bigint unsigned | NO |  | Newest record behind the spell |
| `row_hash` | char(64) | NO |  | Hash of the spell; equal hash = identical row |
| `recorded_at` | datetime(6) | NO |  | System time inserted |
| `recorded_run_id` | bigint unsigned | YES |  | Build run that inserted it |
| `retired_at` | datetime(6) | YES |  | System time a rebuild replaced it; NULL = live |
| `is_live` | tinyint (generated) | YES |  | 1 while live, NULL once retired |

### wh_property_snapshot

Table. Property figures per reporting date, with lineage to page and cell.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `snapshot_id` | bigint unsigned | NO | PRI | Surrogate key |
| `property_id` | bigint unsigned | NO | MUL | Property |
| `as_of_date` | date | NO |  | Date the figures describe (valid time) |
| `square_feet` | decimal(16,2) | YES |  | Rentable square feet |
| `units` | int unsigned | YES |  | Units, keys or beds |
| `occupancy_pct` | decimal(7,4) | YES |  | Occupancy percent |
| `annualized_rent` | decimal(20,2) | YES |  | Annualized base rent, USD |
| `ownership_pct` | decimal(7,4) | YES |  | Company ownership share |
| `known_from` | date | NO |  | Knowledge time: publication date of the source document |
| `source_doc_id` | bigint unsigned | NO |  | Source document |
| `source_item_id` | bigint unsigned | YES |  | Source line item |
| `source_page_no` | int unsigned | YES |  | Source page |
| `recorded_at` | datetime(6) | NO |  | System time inserted |
| `retired_at` | datetime(6) | YES |  | System time replaced; NULL = live |

### wh_property_xref

Table. Record to property links with method, score and review status. Manual links survive rebuilds.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `xref_id` | bigint unsigned | NO | PRI | Surrogate key |
| `record_id` | bigint unsigned | NO | MUL | Source record |
| `property_id` | bigint unsigned | NO | MUL | Property it was matched to |
| `method` | enum('source_ref','address','name_city','geo','new','manual') | NO |  | Rule that made the link (new = no match, a property was created) |
| `score` | decimal(5,4) | NO |  | Match strength 0..1 |
| `review_status` | enum('auto','needs_review','confirmed','rejected') | NO | MUL | needs_review: close call, see candidates; confirmed or rejected by a person |
| `candidates` | json | YES |  | Other properties considered, with scores, for review |
| `matcher_version` | varchar(16) | NO |  | Matcher version |
| `run_id` | bigint unsigned | YES |  | Build run |
| `matched_at` | datetime(6) | NO |  | System time linked |
| `retired_at` | datetime(6) | YES |  | System time the link was replaced (re-match or merge); NULL = live |
| `is_live` | tinyint (generated) | YES |  | 1 while live, NULL once retired |

### wh_tenant_exposure

Table. Top tenant, customer and operator concentration from supplementals.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `tenant_exp_id` | bigint unsigned | NO | PRI | Surrogate key |
| `company_id` | int unsigned | NO | MUL | Company (landlord) |
| `as_of_date` | date | NO |  | Date of the list |
| `tenant_name` | varchar(255) | NO | MUL | Tenant, customer or operator as printed |
| `tenant_rank` | smallint | YES |  | Rank as printed |
| `industry` | varchar(128) | YES |  | Industry or line of trade as printed |
| `credit_rating` | varchar(32) | YES |  | Credit rating as printed |
| `n_properties` | int | YES |  | Properties or locations |
| `n_leases` | int | YES |  | Leases |
| `square_feet` | decimal(16,2) | YES |  | Square feet leased |
| `annualized_rent` | decimal(20,2) | YES |  | Annualized rent, USD |
| `rent_pct` | decimal(9,4) | YES |  | Percent of total annualized rent (or of the measure the table states) |
| `wale_years` | decimal(8,2) | YES |  | Weighted average remaining lease term, years |
| `is_total` | tinyint(1) | NO |  | 1 for a total row such as Top 20 total |
| `known_from` | date | NO |  | Knowledge time: publication date of the source document |
| `known_to` | date | YES |  | Knowledge time: date a later document gave a different value; NULL = still the latest |
| `version` | smallint unsigned | NO |  | 1 = first reported, 2+ = later revisions of the same row |
| `confidence` | decimal(4,3) | YES |  | Parser confidence 0..1 of the source record |
| `source_doc_id` | bigint unsigned | NO |  | stg_document row |
| `source_sha256` | char(64) | YES |  | Raw object (exact bytes) the value was read from |
| `source_page_no` | int unsigned | YES |  | Page in the source document |
| `source_table_id` | bigint unsigned | YES |  | stg_doc_table row |
| `source_record_id` | bigint unsigned | YES |  | stg_doc_record row |
| `row_hash` | char(64) | NO | MUL | Hash of key, knowledge time, values and lineage; equal hash = identical row |
| `recorded_at` | datetime(6) | NO |  | System time: when this row entered the warehouse |
| `recorded_run_id` | bigint unsigned | YES |  | Build run that inserted it |
| `retired_at` | datetime(6) | YES |  | System time: when a rebuild replaced it; NULL = live |
| `retired_run_id` | bigint unsigned | YES |  | Build run that retired it |
| `is_live` | tinyint (generated) | YES |  | 1 while live, NULL once retired |

## dl_* Delivery: client contract views (changing them is a breaking change)

- [`dl_companies`](#dl_companies) (view): VIEW
- [`dl_company_profile`](#dl_company_profile) (view): VIEW
- [`dl_debt_instruments`](#dl_debt_instruments) (view): VIEW
- [`dl_debt_maturities`](#dl_debt_maturities) (view): VIEW
- [`dl_development_pipeline`](#dl_development_pipeline) (view): VIEW
- [`dl_documents`](#dl_documents) (view): VIEW
- [`dl_filings`](#dl_filings) (view): VIEW
- [`dl_lease_expirations`](#dl_lease_expirations) (view): VIEW
- [`dl_metric_catalog`](#dl_metric_catalog) (view): VIEW
- [`dl_metrics`](#dl_metrics) (view): VIEW
- [`dl_metrics_history`](#dl_metrics_history) (view): VIEW
- [`dl_properties`](#dl_properties) (view): VIEW
- [`dl_property_metrics`](#dl_property_metrics) (view): VIEW
- [`dl_top_tenants`](#dl_top_tenants) (view): VIEW

### dl_companies

View.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `company_id` | int unsigned | NO |  | Surrogate key used everywhere |
| `cik` | int unsigned | NO |  | SEC Central Index Key (permanent company id) |
| `ticker` | varchar(16) | YES |  | Current primary ticker |
| `name` | varchar(255) | NO |  | Current SEC conformed name |
| `exchange` | varchar(32) | YES |  | Primary listing exchange |
| `sector` | varchar(64) | YES |  | Property sector (from the universe file) |
| `reit_type` | varchar(32) | YES |  | Equity, mortgage or hybrid |
| `fiscal_year_end` | char(4) | YES |  | Fiscal year end as MMDD |
| `in_universe` | tinyint(1) | NO |  | 1 if currently tracked; delisted companies stay with 0 |
| `updated_at` | datetime(6) | NO |  | Last change (UTC) |

### dl_company_profile

View.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `ticker` | varchar(16) | YES |  | Current primary ticker |
| `company_name` | varchar(255) | NO |  | Current SEC conformed name |
| `metric_code` | varchar(48) | NO |  | Stable public code used in client files, e.g. FFO_PER_SHARE |
| `metric_name` | varchar(128) | NO |  | Display name |
| `category` | varchar(48) | NO |  | Grouping, e.g. Earnings, Balance Sheet |
| `grain` | enum('company','segment','property') | NO |  | Level the metric describes: company (incl. segments of it) or a single property |
| `relevance` | enum('core','common','optional') | YES |  | core: every company in the sector reports it; common: most do; optional: some do |
| `unit_type` | enum('USD','USD_PER_SHARE','PCT','SF','COUNT','SHARES','RATIO','YEARS','USD_PER_SF','USD_PER_UNIT','MW') | NO |  | Unit family (USD_PER_UNIT: per room, unit, bed or occupied room per night or month) |
| `period_type` | enum('Q','YTD','FY','TTM','INSTANT') | YES |  | Q quarter, YTD year to date, FY fiscal year, INSTANT balance |
| `period_end` | date | YES |  | Business period end or balance date (valid time) |
| `segment` | varchar(128) | YES |  | Total or a segment label |
| `value` | decimal(28,6) | YES |  | Value in unit |
| `unit` | varchar(32) | YES |  | Unit, e.g. USD, USD/shares, pct |
| `source_type` | enum('xbrl','derived','supplemental','manual') | YES |  | xbrl: as tagged; derived: computed from xbrl (e.g. Q4 = FY - 9M); supplemental: parsed from a document; manual: analyst entry |
| `published_date` | date | YES |  | Knowledge time: date the value was published (filing or document date) |
| `source_document_url` | varchar(2048) | YES |  | Where it came from (file:// URI for archive imports) |
| `source_page_no` | int unsigned | YES |  | Page in the source document |
| `has_value` | int | NO |  |  |

### dl_debt_instruments

View.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `ticker` | varchar(16) | YES |  | Current primary ticker |
| `as_of_date` | date | NO |  | Date of the schedule |
| `segment` | varchar(128) | NO |  | Sub-heading, e.g. Unsecured, Mortgages |
| `instrument` | varchar(255) | NO |  | Loan or note as printed, e.g. 3.600% notes due 2026 |
| `debt_type` | varchar(64) | YES |  | Type column as printed |
| `principal` | decimal(20,2) | YES |  | Principal outstanding, USD |
| `rate` | decimal(9,4) | YES |  | Stated interest rate, percent |
| `effective_rate` | decimal(9,4) | YES |  | Effective interest rate, percent |
| `maturity_text` | varchar(64) | YES |  | Maturity as printed |
| `maturity_year` | smallint | YES |  | Maturity year read from the text |
| `is_total` | tinyint(1) | NO |  | 1 for a total row |
| `published_date` | date | NO |  | Knowledge time: publication date of the source document |
| `source_document_url` | varchar(2048) | NO |  | Where it came from (file:// URI for archive imports) |
| `source_page_no` | int unsigned | YES |  | Page in the source document |

### dl_debt_maturities

View.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `ticker` | varchar(16) | YES |  | Current primary ticker |
| `as_of_date` | date | NO |  | Balance sheet date of the schedule |
| `maturity_year` | smallint | YES |  | Year the debt matures; NULL for Thereafter and Total buckets |
| `bucket_label` | varchar(64) | YES |  | Bucket as printed: a year, Thereafter, Total |
| `debt_type` | varchar(64) | NO |  | Secured, unsecured, line of credit, Total |
| `amount` | decimal(20,2) | NO |  | Principal maturing, USD |
| `wtd_avg_rate` | decimal(7,4) | YES |  | Weighted average rate of that bucket, percent |
| `pct_of_total` | decimal(9,4) | YES |  | Percent of total debt maturing in the bucket |
| `is_total` | tinyint(1) | NO |  | 1 for the total row |
| `published_date` | date | NO |  | Knowledge time: publication date |
| `source_document_url` | varchar(2048) | NO |  | Where it came from (file:// URI for archive imports) |
| `source_page_no` | int unsigned | YES |  | Source page |

### dl_development_pipeline

View.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `ticker` | varchar(16) | YES |  | Current primary ticker |
| `as_of_date` | date | NO |  | Date of the schedule |
| `segment` | varchar(128) | NO |  | Sub-heading the row sat under (e.g. Under construction, Completed), or Total |
| `project_name` | varchar(255) | NO |  | Project, region or bucket as printed |
| `market` | varchar(128) | YES |  | Market as printed |
| `property_type` | varchar(64) | YES |  | Property type as printed |
| `units` | int | YES |  | Units, homes, beds, keys or MW |
| `square_feet` | decimal(16,2) | YES |  | Square feet |
| `total_cost` | decimal(20,2) | YES |  | Estimated total cost or investment, USD |
| `cost_to_date` | decimal(20,2) | YES |  | Cost incurred to date, USD |
| `remaining_cost` | decimal(20,2) | YES |  | Cost remaining to fund, USD |
| `pct_leased` | decimal(9,4) | YES |  | Percent leased or pre-leased |
| `stabilized_yield` | decimal(9,4) | YES |  | Expected stabilized yield, percent |
| `ownership_pct` | decimal(9,4) | YES |  | Company ownership share, percent |
| `completion_text` | varchar(64) | YES |  | Expected completion or stabilization as printed |
| `is_total` | tinyint(1) | NO |  | 1 for a total row |
| `published_date` | date | NO |  | Knowledge time: publication date of the source document |
| `source_document_url` | varchar(2048) | NO |  | Where it came from (file:// URI for archive imports) |
| `source_page_no` | int unsigned | YES |  | Page in the source document |

### dl_documents

View.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `ticker` | varchar(16) | YES |  | Current primary ticker |
| `cik` | int unsigned | YES |  | SEC Central Index Key (permanent company id) |
| `doc_id` | bigint unsigned | NO |  | Surrogate key |
| `doc_kind` | enum('supplemental','earnings_release','presentation','exhibit_other','unknown') | NO |  | What the document is |
| `source` | enum('sec_8k_exhibit','ir_website','email','manual_archive') | NO |  | How we got it |
| `period_end_date` | date | YES |  | Reporting period end the document covers |
| `published_date` | date | YES |  | Date published (filing date for 8-Ks) = when the market could know it |
| `original_filename` | varchar(255) | YES |  | File name as published |
| `source_url` | varchar(2048) | NO |  | Where it came from (file:// URI for archive imports) |
| `accession_no` | char(20) | YES |  | SEC accession, for 8-K exhibits |
| `status` | enum('registered','fetched','parsed','needs_review','failed','duplicate','skipped') | NO |  | Lifecycle state |

### dl_filings

View.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `ticker` | varchar(16) | YES |  | Current primary ticker |
| `cik` | int unsigned | NO |  | SEC Central Index Key (permanent company id) |
| `accession_no` | char(20) | NO |  | SEC accession number 0000000000-00-000000 |
| `form_type` | varchar(32) | NO |  | Form, e.g. 10-K, 10-Q, 8-K, 10-K/A |
| `filing_date` | date | NO |  | Date filed with SEC (when the market could know it) |
| `report_date` | date | YES |  | Period of report |
| `items` | varchar(255) | YES |  | 8-K item numbers, e.g. 2.02,9.01 |
| `index_url` | varchar(512) | NO |  | EDGAR filing index page |
| `primary_doc_url` | varchar(512) | YES |  | URL of the primary document |

### dl_lease_expirations

View.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `ticker` | varchar(16) | YES |  | Current primary ticker |
| `as_of_date` | date | NO |  | Date of the schedule |
| `segment` | varchar(128) | NO |  | Sub-heading the row sat under, or Total |
| `bucket_label` | varchar(64) | NO |  | Bucket as printed: a year, Thereafter, Month-to-month, Vacant, Total |
| `expiry_year` | smallint | YES |  | Year, when the bucket is one year |
| `n_leases` | int | YES |  | Leases expiring |
| `n_properties` | int | YES |  | Properties with leases expiring |
| `square_feet` | decimal(16,2) | YES |  | Square feet expiring |
| `annualized_rent` | decimal(20,2) | YES |  | Annualized rent expiring, USD |
| `rent_pct` | decimal(9,4) | YES |  | Percent of total annualized rent |
| `rent_psf` | decimal(12,4) | YES |  | Annualized rent per square foot, USD |
| `is_total` | tinyint(1) | NO |  | 1 for the total row |
| `published_date` | date | NO |  | Knowledge time: publication date of the source document |
| `source_document_url` | varchar(2048) | NO |  | Where it came from (file:// URI for archive imports) |
| `source_page_no` | int unsigned | YES |  | Page in the source document |

### dl_metric_catalog

View.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `sector_code` | varchar(32) | NO |  | Stable code, e.g. apartments, net_lease, senior_housing |
| `sector_name` | varchar(96) | NO |  | Display name |
| `metric_code` | varchar(48) | NO |  | Stable public code used in client files, e.g. FFO_PER_SHARE |
| `metric_name` | varchar(128) | NO |  | Display name |
| `category` | varchar(48) | NO |  | Grouping, e.g. Earnings, Balance Sheet |
| `unit_type` | enum('USD','USD_PER_SHARE','PCT','SF','COUNT','SHARES','RATIO','YEARS','USD_PER_SF','USD_PER_UNIT','MW') | NO |  | Unit family (USD_PER_UNIT: per room, unit, bed or occupied room per night or month) |
| `period_nature` | enum('duration','instant') | NO |  | Flow over a period or balance at a date |
| `grain` | enum('company','segment','property') | NO |  | Level the metric describes: company (incl. segments of it) or a single property |
| `is_non_gaap` | tinyint(1) | NO |  | 1 for non-GAAP measures |
| `is_nareit_defined` | tinyint(1) | NO |  | 1 if Nareit publishes the definition |
| `relevance` | enum('core','common','optional') | NO |  | core: every company in the sector reports it; common: most do; optional: some do |
| `defined_for` | varchar(32) | NO |  | Sector, or a group (equity, mortgage, all) covering its children |
| `definition` | text | YES |  | Definition shown to clients |

### dl_metrics

View.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `ticker` | varchar(16) | YES |  | Current primary ticker |
| `cik` | int unsigned | NO |  | SEC Central Index Key (permanent company id) |
| `metric_code` | varchar(48) | NO |  | Stable public code used in client files, e.g. FFO_PER_SHARE |
| `metric_name` | varchar(128) | NO |  | Display name |
| `category` | varchar(48) | NO |  | Grouping, e.g. Earnings, Balance Sheet |
| `period_type` | enum('Q','YTD','FY','TTM','INSTANT') | NO |  | Q quarter, YTD year to date, FY fiscal year, INSTANT balance |
| `period_start` | date | YES |  | Business period start (valid time); NULL for INSTANT |
| `period_end` | date | NO |  | Business period end or balance date (valid time) |
| `fiscal_year` | smallint | NO |  | Fiscal year (named for the calendar year it ends in) |
| `fiscal_quarter` | tinyint | YES |  | 1..4 for Q, YTD and INSTANT; NULL for FY |
| `segment` | varchar(128) | NO |  | Total or a segment label |
| `value` | decimal(28,6) | NO |  | Value in unit |
| `unit` | varchar(32) | NO |  | Unit, e.g. USD, USD/shares, pct |
| `source_type` | enum('xbrl','derived','supplemental','manual') | NO |  | xbrl: as tagged; derived: computed from xbrl (e.g. Q4 = FY - 9M); supplemental: parsed from a document; manual: analyst entry |
| `version` | smallint unsigned | NO |  | 1 = first reported, 2+ = restatements (knowledge time order) |
| `published_date` | date | NO |  | Knowledge time: date the value was published (filing or document date) |
| `is_preferred` | int | NO |  |  |
| `source_accession` | char(20) | YES |  | SEC filing the value comes from |
| `source_filing_url` | varchar(512) | YES |  | EDGAR filing index page |
| `source_document_url` | varchar(2048) | YES |  | Where it came from (file:// URI for archive imports) |
| `source_page_no` | int unsigned | YES |  | Page in the source document |
| `value_id` | bigint unsigned | NO |  | Surrogate key |

### dl_metrics_history

View.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `ticker` | varchar(16) | YES |  | Current primary ticker |
| `cik` | int unsigned | NO |  | SEC Central Index Key (permanent company id) |
| `metric_code` | varchar(48) | NO |  | Stable public code used in client files, e.g. FFO_PER_SHARE |
| `period_type` | enum('Q','YTD','FY','TTM','INSTANT') | NO |  | Q quarter, YTD year to date, FY fiscal year, INSTANT balance |
| `period_start` | date | YES |  | Business period start (valid time); NULL for INSTANT |
| `period_end` | date | NO |  | Business period end or balance date (valid time) |
| `fiscal_year` | smallint | NO |  | Fiscal year (named for the calendar year it ends in) |
| `fiscal_quarter` | tinyint | YES |  | 1..4 for Q, YTD and INSTANT; NULL for FY |
| `segment` | varchar(128) | NO |  | Total or a segment label |
| `value` | decimal(28,6) | NO |  | Value in unit |
| `unit` | varchar(32) | NO |  | Unit, e.g. USD, USD/shares, pct |
| `source_type` | enum('xbrl','derived','supplemental','manual') | NO |  | xbrl: as tagged; derived: computed from xbrl (e.g. Q4 = FY - 9M); supplemental: parsed from a document; manual: analyst entry |
| `version` | smallint unsigned | NO |  | 1 = first reported, 2+ = restatements (knowledge time order) |
| `published_date` | date | NO |  | Knowledge time: date the value was published (filing or document date) |
| `superseded_date` | date | YES |  | Knowledge time: date a different value was published; NULL = still the latest |
| `source_accession` | char(20) | YES |  | SEC filing the value comes from |
| `source_doc_id` | bigint unsigned | YES |  | stg_document row (supplemental) |
| `source_page_no` | int unsigned | YES |  | Page in the source document |
| `value_id` | bigint unsigned | NO |  | Surrogate key |

### dl_properties

View.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `property_id` | bigint unsigned | NO |  | Stable property id: assigned once, never reused, survives sales between REITs |
| `name` | varchar(255) | YES |  | Canonical name (highest-priority, newest source) |
| `address_line` | varchar(255) | YES |  | Canonical street address |
| `city` | varchar(128) | YES |  | City |
| `state` | varchar(64) | YES |  | State or province |
| `postal_code` | varchar(16) | YES |  | Postal code |
| `country` | char(2) | YES |  | ISO country code |
| `market` | varchar(128) | YES |  | Market or MSA as reported |
| `type_code` | varchar(32) | YES |  | Property type |
| `sector` | varchar(32) | YES |  | Sector of the type |
| `rentable_sf` | decimal(16,2) | YES |  | Latest rentable square feet |
| `unit_count` | decimal(14,2) | YES |  | Latest size in unit_kind |
| `unit_kind` | enum('sf','units','beds','keys','sites','acres','towers','screens','mw','other') | YES |  | Unit of unit_count |
| `land_acres` | decimal(14,3) | YES |  | Land area in acres |
| `year_built` | smallint unsigned | YES |  | Year built |
| `latitude` | decimal(9,6) | YES |  | Latitude (WGS84) |
| `longitude` | decimal(9,6) | YES |  | Longitude (WGS84) |
| `geo_precision` | enum('rooftop','street','zip','city','reported','none') | NO |  | Precision of the point |
| `status` | enum('active','inactive','merged') | NO |  | active: owned by a tracked REIT now; inactive: no current owner in the universe; merged: duplicate, see merged_into_property_id |
| `owner_ticker` | varchar(16) | YES |  | Current primary ticker |
| `owner_cik` | int unsigned | NO |  | SEC Central Index Key (permanent company id) |
| `ownership_pct` | decimal(7,4) | YES |  | Latest reported share, percent (NULL = not reported, usually 100) |
| `interest_type` | enum('fee','ground_lease','leasehold','joint_venture','mortgage','unknown') | YES |  | Latest reported interest type |
| `acquired_date` | date | YES |  | Start of ownership |
| `acquired_basis` | enum('reported','first_seen') | NO |  | reported by a source, or the first as-of date it was listed |
| `first_seen` | date | YES |  | Earliest as-of date of any linked record |
| `last_seen` | date | YES |  | Latest as-of date of any linked record |
| `n_source_kinds` | tinyint unsigned | NO |  | Distinct source kinds that list it (more = better confirmed) |
| `updated_at` | datetime(6) | NO |  | Last change (UTC) |

### dl_property_metrics

View.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `ticker` | varchar(16) | YES |  | Current primary ticker |
| `property_id` | bigint unsigned | YES |  | Property it was matched to |
| `property_record_id` | bigint unsigned | YES |  | stg_property_record row for this property in this document; the property master links it to a property_id through wh_property_xref |
| `property_name` | varchar(255) | NO |  | Property name as printed |
| `city` | varchar(128) | YES |  | City as printed |
| `state` | varchar(64) | YES |  | State as printed |
| `market` | varchar(128) | YES |  | Market or MSA as printed |
| `property_type` | varchar(64) | YES |  | Property type as printed |
| `metric_code` | varchar(48) | NO |  | Stable public code used in client files, e.g. FFO_PER_SHARE |
| `metric_name` | varchar(128) | NO |  | Display name |
| `as_of_date` | date | NO |  | Date or period end the figure describes (valid time) |
| `value` | decimal(28,6) | NO |  | Value in unit |
| `unit` | varchar(32) | NO |  | Unit, e.g. USD, sf, pct, count |
| `published_date` | date | NO |  | Knowledge time: publication date of the source document |
| `confidence` | decimal(4,3) | YES |  | Parser confidence 0..1 of the source record |
| `source_document_url` | varchar(2048) | NO |  | Where it came from (file:// URI for archive imports) |
| `source_page_no` | int unsigned | YES |  | Page in the source document |
| `property_metric_id` | bigint unsigned | NO |  | Surrogate key |

### dl_top_tenants

View.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `ticker` | varchar(16) | YES |  | Current primary ticker |
| `as_of_date` | date | NO |  | Date of the list |
| `tenant_rank` | smallint | YES |  | Rank as printed |
| `tenant_name` | varchar(255) | NO |  | Tenant, customer or operator as printed |
| `industry` | varchar(128) | YES |  | Industry or line of trade as printed |
| `credit_rating` | varchar(32) | YES |  | Credit rating as printed |
| `n_properties` | int | YES |  | Properties or locations |
| `n_leases` | int | YES |  | Leases |
| `square_feet` | decimal(16,2) | YES |  | Square feet leased |
| `annualized_rent` | decimal(20,2) | YES |  | Annualized rent, USD |
| `rent_pct` | decimal(9,4) | YES |  | Percent of total annualized rent (or of the measure the table states) |
| `wale_years` | decimal(8,2) | YES |  | Weighted average remaining lease term, years |
| `is_total` | tinyint(1) | NO |  | 1 for a total row such as Top 20 total |
| `published_date` | date | NO |  | Knowledge time: publication date of the source document |
| `source_document_url` | varchar(2048) | NO |  | Where it came from (file:// URI for archive imports) |
| `source_page_no` | int unsigned | YES |  | Page in the source document |

