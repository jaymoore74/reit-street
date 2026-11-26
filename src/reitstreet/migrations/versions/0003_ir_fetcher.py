"""IR fetcher: per-company fetch profiles, robots.txt cache, discovered links, check log.

ref_ir_profile  how each company's IR site is fetched (strategy, platform, whether a
                quarterly supplemental is expected). Seeded from the versioned file
                src/reitstreet/reference/data/ir_profiles.csv, never edited by hand in prod.
ref_web_host    robots.txt state per host (RFC 9309), so every run honors it.
stg_ir_link     every document link ever seen on an IR site, classified, with the period
                it covers. New links are what the fetcher downloads.
ops_ir_check    one row per company per run: what the fetcher saw. Drives coverage,
                layout-break and failure alerts.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-26
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

T = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"

DOC_KINDS = "'supplemental','earnings_release','presentation','exhibit_other','unknown'"
OUTCOMES = (
    "'ok','not_modified','no_documents','js_only','layout_break','robots_blocked','http_error',"
    "'needs_headless','not_configured','no_source'"
)

TABLES: list[tuple[str, str]] = [
    (
        "ref_ir_profile",
        f"""
CREATE TABLE ref_ir_profile (
  company_id            INT UNSIGNED NOT NULL COMMENT 'Company',
  strategy              ENUM('static_html','q4_feed','headless','sec_8k','none') NOT NULL DEFAULT 'static_html' COMMENT 'First fetch strategy to try for the results page; fallbacks follow in code',
  platform              VARCHAR(32) NULL COMMENT 'IR site vendor or layout family, e.g. q4cdn, static-files, cloudfront, wordpress',
  expects_supplemental  TINYINT(1) NOT NULL DEFAULT 1 COMMENT '1 if the company publishes a quarterly supplemental; drives the overdue check',
  probe_status          ENUM('works','js_only','no_supp','error','not_probed') NOT NULL DEFAULT 'not_probed' COMMENT 'Result of the last manual coverage probe (from the seed file)',
  probe_supp_links      SMALLINT UNSIGNED NULL COMMENT 'Supplemental links the probe saw on the results page',
  probe_latest_period   VARCHAR(16) NULL COMMENT 'Latest supplemental period the probe saw, e.g. Q3 2026',
  probe_date            DATE NULL COMMENT 'When the probe ran',
  notes                 VARCHAR(1024) NULL COMMENT 'Free-text notes from the seed file',
  profile_source        VARCHAR(255) NULL COMMENT 'Seed file and hash this row came from',
  updated_at            DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'Last change (UTC)',
  PRIMARY KEY (company_id),
  CONSTRAINT fk_irp_company FOREIGN KEY (company_id) REFERENCES ref_company (company_id)
) {T} COMMENT='How each company IR site is fetched. Seeded from reference/data/ir_profiles.csv.'""",
    ),
    (
        "ref_web_host",
        f"""
CREATE TABLE ref_web_host (
  host                VARCHAR(255) NOT NULL COMMENT 'Lower-case host name',
  robots_status       SMALLINT NULL COMMENT 'HTTP status of the last robots.txt fetch; NULL when unreachable',
  robots_policy       ENUM('parsed','allow_all','disallow_all') NOT NULL COMMENT 'RFC 9309: 2xx parsed, 4xx allow all, 5xx or unreachable disallow all',
  robots_sha256       CHAR(64) NULL COMMENT 'Raw object holding the robots.txt body',
  crawl_delay_s       DECIMAL(8,2) NULL COMMENT 'Crawl-delay for our user agent, if any',
  robots_fetched_at   DATETIME(6) NOT NULL COMMENT 'UTC time robots.txt was last fetched',
  PRIMARY KEY (host),
  CONSTRAINT fk_host_object FOREIGN KEY (robots_sha256) REFERENCES raw_object (sha256)
) {T} COMMENT='robots.txt state per web host, refreshed by the IR fetcher (default every 24 hours).'""",
    ),
    (
        "stg_ir_link",
        f"""
CREATE TABLE stg_ir_link (
  link_id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  company_id        INT UNSIGNED NOT NULL COMMENT 'Company',
  source_id         INT UNSIGNED NULL COMMENT 'ref_ir_source page the link was found on',
  url               VARCHAR(2048) NOT NULL COMMENT 'Absolute document URL (no fragment)',
  url_hash          CHAR(64) AS (SHA2(url, 256)) STORED COMMENT 'Hash of url, for uniqueness',
  anchor_text       VARCHAR(512) NULL COMMENT 'Link text as published',
  context_text      VARCHAR(512) NULL COMMENT 'Nearby heading or row text, used for period inference',
  doc_kind          ENUM({DOC_KINDS}) NOT NULL COMMENT 'Classification from link text and URL',
  kind_reason       VARCHAR(64) NULL COMMENT 'Rule that set doc_kind',
  fiscal_year       SMALLINT UNSIGNED NULL COMMENT 'Fiscal year the document covers',
  fiscal_quarter    TINYINT UNSIGNED NULL COMMENT 'Fiscal quarter (1-4) the document covers',
  period_end_date   DATE NULL COMMENT 'Period end the document covers',
  period_source     VARCHAR(32) NULL COMMENT 'Where the period came from: anchor, url, context, feed, pdf_text',
  strategy          VARCHAR(16) NOT NULL COMMENT 'Fetch strategy that found it',
  status            ENUM('new','downloaded','skipped','failed','robots_blocked') NOT NULL DEFAULT 'new' COMMENT 'Download state',
  status_note       VARCHAR(512) NULL COMMENT 'Why skipped or failed',
  doc_id            BIGINT UNSIGNED NULL COMMENT 'Document registered from this link',
  first_seen_at     DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'UTC time first seen = discovery time',
  last_seen_at      DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'UTC time last seen on the page',
  first_run_id      BIGINT UNSIGNED NULL COMMENT 'Run that first saw it',
  PRIMARY KEY (link_id),
  UNIQUE KEY uq_link (company_id, url_hash),
  KEY ix_kind_period (company_id, doc_kind, period_end_date),
  KEY ix_status (status),
  CONSTRAINT fk_link_company FOREIGN KEY (company_id) REFERENCES ref_company (company_id),
  CONSTRAINT fk_link_doc FOREIGN KEY (doc_id) REFERENCES stg_document (doc_id)
) {T} COMMENT='Every document link seen on a company IR site. New supplemental links are downloaded.'""",
    ),
    (
        "ops_ir_check",
        f"""
CREATE TABLE ops_ir_check (
  check_id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  run_id          BIGINT UNSIGNED NULL COMMENT 'Job run',
  company_id      INT UNSIGNED NOT NULL COMMENT 'Company',
  source_id       INT UNSIGNED NULL COMMENT 'ref_ir_source page checked; NULL when the company has none',
  strategy        VARCHAR(16) NOT NULL COMMENT 'Strategy that produced this outcome',
  outcome         ENUM({OUTCOMES}) NOT NULL COMMENT 'What happened',
  http_status     SMALLINT NULL COMMENT 'Status of the page request',
  links_found     INT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'Document links on the page',
  supp_links      INT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'Of which classified supplemental',
  new_links       INT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'Links not seen before',
  new_docs        INT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'Documents downloaded and registered',
  latest_supp_period DATE NULL COMMENT 'Latest supplemental period end seen on the page',
  note            VARCHAR(1024) NULL COMMENT 'Detail',
  checked_at      DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'UTC time',
  PRIMARY KEY (check_id),
  KEY ix_company_time (company_id, checked_at),
  KEY ix_run (run_id),
  CONSTRAINT fk_irc_company FOREIGN KEY (company_id) REFERENCES ref_company (company_id)
) {T} COMMENT='One row per company per IR fetch run. Source of coverage reporting and IR alerts.'""",
    ),
]

ALTERS: list[tuple[str, str]] = [
    (
        "ref_ir_source",
        """
ALTER TABLE ref_ir_source
  ADD COLUMN http_etag          VARCHAR(255) NULL COMMENT 'ETag of the last 200 response, sent back as If-None-Match' AFTER last_sha256,
  ADD COLUMN http_last_modified VARCHAR(64) NULL COMMENT 'Last-Modified of the last 200 response, sent back as If-Modified-Since' AFTER http_etag,
  ADD COLUMN last_outcome       VARCHAR(16) NULL COMMENT 'Outcome of the last check (see ops_ir_check.outcome)' AFTER http_last_modified,
  ADD COLUMN last_link_count    INT UNSIGNED NULL COMMENT 'Supplemental links seen at the last successful page check' AFTER last_outcome,
  ADD COLUMN max_link_count     INT UNSIGNED NULL COMMENT 'Most supplemental links ever seen; zero after a positive count = layout break' AFTER last_link_count""",
    ),
    (
        "stg_document",
        """
ALTER TABLE stg_document
  ADD COLUMN title          VARCHAR(512) NULL COMMENT 'Title as published (link text, feed title or PDF title)' AFTER original_filename,
  ADD COLUMN link_id        BIGINT UNSIGNED NULL COMMENT 'stg_ir_link the document was downloaded from' AFTER filing_id,
  ADD COLUMN kind_source    VARCHAR(32) NULL COMMENT 'What set doc_kind: link rule, feed, archive, sec' AFTER doc_kind,
  ADD COLUMN period_source  VARCHAR(32) NULL COMMENT 'What set period_end_date: anchor, url, context, feed, pdf_text, manifest' AFTER period_end_date""",
    ),
]

DROPS = {
    "ref_ir_source": "ALTER TABLE ref_ir_source DROP COLUMN http_etag, DROP COLUMN http_last_modified, "
    "DROP COLUMN last_outcome, DROP COLUMN last_link_count, DROP COLUMN max_link_count",
    "stg_document": "ALTER TABLE stg_document DROP COLUMN title, DROP COLUMN link_id, DROP COLUMN kind_source, "
    "DROP COLUMN period_source",
}

VIEWS: list[tuple[str, str]] = [
    (
        "ops_ir_coverage",
        """
CREATE OR REPLACE SQL SECURITY INVOKER VIEW ops_ir_coverage AS
SELECT c.company_id, c.ticker, c.name, c.reit_type,
       COALESCE(p.strategy, 'static_html') AS strategy, p.platform, COALESCE(p.expects_supplemental, 1) AS expects_supplemental,
       COALESCE(p.probe_status, 'not_probed') AS probe_status, p.probe_supp_links, p.probe_latest_period, p.probe_date,
       k.checked_at AS last_checked_at, k.strategy AS last_strategy, k.outcome AS last_outcome, k.http_status AS last_http_status,
       k.supp_links AS last_supp_links, k.latest_supp_period AS last_page_supp_period,
       (SELECT MAX(d.period_end_date) FROM stg_document d
         WHERE d.company_id = c.company_id AND d.doc_kind = 'supplemental' AND d.status IN ('fetched','parsed','needs_review')) AS latest_supplemental_held,
       CASE
         WHEN k.outcome IS NULL THEN COALESCE(p.probe_status, 'not_probed')
         WHEN k.outcome IN ('ok','not_modified') AND k.supp_links > 0 THEN 'works'
         WHEN k.outcome IN ('js_only','needs_headless') THEN 'needs_headless'
         WHEN k.outcome = 'robots_blocked' THEN 'blocked'
         WHEN k.outcome IN ('http_error','layout_break') THEN 'broken'
         WHEN k.outcome IN ('ok','no_documents','not_modified') THEN 'no_supp'
         ELSE k.outcome
       END AS coverage
FROM ref_company c
LEFT JOIN ref_ir_profile p ON p.company_id = c.company_id
LEFT JOIN ops_ir_check k ON k.check_id = (
    SELECT MAX(k2.check_id) FROM ops_ir_check k2 WHERE k2.company_id = c.company_id AND k2.source_id IS NOT NULL)
WHERE c.in_universe = 1""",
    ),
]


def upgrade() -> None:
    for _name, ddl in TABLES:
        op.execute(ddl)
    for _name, ddl in ALTERS:
        op.execute(ddl)
    for _name, ddl in VIEWS:
        op.execute(ddl)


def downgrade() -> None:
    for name, _ddl in reversed(VIEWS):
        op.execute(f"DROP VIEW IF EXISTS {name}")
    for name, _ddl in reversed(ALTERS):
        op.execute(DROPS[name])
    for name, _ddl in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {name}")
