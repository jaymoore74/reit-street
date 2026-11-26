"""Supplemental PDF parser: sector metric catalog, parse overrides, typed records and
property-, tenant-, development-, debt- and lease-level warehouse tables.

ref_sector, ref_company_sector, ref_metric_sector
                   which metrics matter for which property sector, so "everything
                   relevant for NNN" is a query, not a judgment call. Seeded from
                   reference/data/sectors.csv, sector_labels.csv, metric_sectors.csv.
ref_parse_override per-company parsing rules (skip a table, force its kind, set its
                   scale, change the confidence bar), as data.
stg_doc_record     one row per row of a property, tenant, development, debt or lease
                   expiration table, typed fields in JSON, with page and position.
wh_*               typed, bitemporal tables built from those records, plus
                   wh_property_metric for per-property figures. wh_property (the
                   property master) is not changed here: it belongs to the property
                   master work, and wh_property_metric links to it by property_id when
                   a property is matched (NULL until then; the raw name, city and state
                   are kept so matching can happen later).

Revision 0005 follows 0003. The property master migration (0004) is developed in
parallel; whichever merges second points its down_revision at the other.

Revision ID: 0005
Revises: 0003
Create Date: 2026-11-26
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0003"
branch_labels = None
depends_on = None

T = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"

RECORD_KINDS = "'property','tenant','development','debt_maturity','debt_detail','lease_expiration'"

# Lineage and system-time columns shared by every record-built warehouse table.
LINEAGE = """
  known_from        DATE NOT NULL COMMENT 'Knowledge time: publication date of the source document',
  known_to          DATE NULL COMMENT 'Knowledge time: date a later document gave a different value; NULL = still the latest',
  version           SMALLINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '1 = first reported, 2+ = later revisions of the same row',
  confidence        DECIMAL(4,3) NULL COMMENT 'Parser confidence 0..1 of the source record',
  source_doc_id     BIGINT UNSIGNED NOT NULL COMMENT 'stg_document row',
  source_sha256     CHAR(64) NULL COMMENT 'Raw object (exact bytes) the value was read from',
  source_page_no    INT UNSIGNED NULL COMMENT 'Page in the source document',
  source_table_id   BIGINT UNSIGNED NULL COMMENT 'stg_doc_table row',
  source_record_id  BIGINT UNSIGNED NULL COMMENT 'stg_doc_record row',
  row_hash          CHAR(64) NOT NULL COMMENT 'Hash of key, knowledge time, values and lineage; equal hash = identical row',
  recorded_at       DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'System time: when this row entered the warehouse',
  recorded_run_id   BIGINT UNSIGNED NULL COMMENT 'Build run that inserted it',
  retired_at        DATETIME(6) NULL COMMENT 'System time: when a rebuild replaced it; NULL = live',
  retired_run_id    BIGINT UNSIGNED NULL COMMENT 'Build run that retired it',
  is_live           TINYINT AS (IF(retired_at IS NULL, 1, NULL)) STORED COMMENT '1 while live, NULL once retired',"""

TABLES: list[tuple[str, str]] = [
    (
        "ref_sector",
        f"""
CREATE TABLE ref_sector (
  sector_code   VARCHAR(32) NOT NULL COMMENT 'Stable code, e.g. apartments, net_lease, senior_housing',
  name          VARCHAR(96) NOT NULL COMMENT 'Display name',
  parent_code   VARCHAR(32) NULL COMMENT 'Broader group: equity or mortgage; all for the two groups',
  description   VARCHAR(512) NULL COMMENT 'What the sector covers',
  sort_order    SMALLINT UNSIGNED NOT NULL DEFAULT 100 COMMENT 'Display order',
  updated_at    DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'Last change (UTC)',
  PRIMARY KEY (sector_code)
) {T} COMMENT='Property sectors. Seeded from reference/data/sectors.csv.'""",
    ),
    (
        "ref_company_sector",
        f"""
CREATE TABLE ref_company_sector (
  company_id    INT UNSIGNED NOT NULL COMMENT 'Company',
  sector_code   VARCHAR(32) NOT NULL COMMENT 'Sector the company invests in',
  is_primary    TINYINT(1) NOT NULL DEFAULT 0 COMMENT '1 for the main sector',
  origin        ENUM('seed','manual') NOT NULL DEFAULT 'seed' COMMENT 'seed rows come from the universe sector label; manual rows are never touched by the seed',
  updated_at    DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'Last change (UTC)',
  PRIMARY KEY (company_id, sector_code),
  KEY ix_sector (sector_code),
  CONSTRAINT fk_cs_company FOREIGN KEY (company_id) REFERENCES ref_company (company_id),
  CONSTRAINT fk_cs_sector FOREIGN KEY (sector_code) REFERENCES ref_sector (sector_code)
) {T} COMMENT='Sectors each company invests in. Drives which metrics are relevant for it.'""",
    ),
    (
        "ref_metric_sector",
        f"""
CREATE TABLE ref_metric_sector (
  metric_id     SMALLINT UNSIGNED NOT NULL COMMENT 'Metric',
  sector_code   VARCHAR(32) NOT NULL COMMENT 'Sector, or a group (equity, mortgage, all) covering its children',
  relevance     ENUM('core','common','optional') NOT NULL COMMENT 'core: every company in the sector reports it; common: most do; optional: some do',
  updated_at    DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'Last change (UTC)',
  PRIMARY KEY (metric_id, sector_code),
  KEY ix_sector (sector_code),
  CONSTRAINT fk_ms_metric FOREIGN KEY (metric_id) REFERENCES ref_metric (metric_id),
  CONSTRAINT fk_ms_sector FOREIGN KEY (sector_code) REFERENCES ref_sector (sector_code)
) {T} COMMENT='Sector metric catalog: which metrics matter for which sector. Seeded from reference/data/metric_sectors.csv.'""",
    ),
    (
        "ref_parse_override",
        f"""
CREATE TABLE ref_parse_override (
  override_id   INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  override_key  VARCHAR(191) NOT NULL COMMENT 'Stable key from the seed file (or manual:<n>) so edits upsert cleanly',
  company_id    INT UNSIGNED NOT NULL COMMENT 'Company whose documents the rule applies to',
  action        ENUM('skip','force_kind','scale','min_confidence') NOT NULL COMMENT 'skip a table; force its kind/orientation; set its scale; change the quarantine bar',
  title_regex   VARCHAR(255) NULL COMMENT 'Applies to tables whose title matches (case-insensitive); NULL = all tables',
  page_from     INT UNSIGNED NULL COMMENT 'First page it applies to',
  page_to       INT UNSIGNED NULL COMMENT 'Last page it applies to',
  params        JSON NULL COMMENT 'Action parameters, e.g. {{"kind": "tenant", "orientation": "records"}} or {{"money": 1000}}',
  valid_from    DATE NULL COMMENT 'Applies to documents for periods ending on or after',
  valid_to      DATE NULL COMMENT 'Applies to documents for periods ending on or before',
  is_active     TINYINT(1) NOT NULL DEFAULT 1 COMMENT '0 = disabled (kept for audit)',
  origin        ENUM('seed','manual') NOT NULL DEFAULT 'seed' COMMENT 'seed rows are managed by the seed command; manual rows never touched by it',
  notes         VARCHAR(512) NULL COMMENT 'Why the rule exists',
  updated_at    DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'Last change (UTC)',
  PRIMARY KEY (override_id),
  UNIQUE KEY uq_key (override_key),
  KEY ix_company (company_id),
  CONSTRAINT fk_po_company FOREIGN KEY (company_id) REFERENCES ref_company (company_id)
) {T} COMMENT='Per-company supplemental parsing rules. Rules are data, so fixing one REIT never changes code.'""",
    ),
    (
        "stg_doc_record",
        f"""
CREATE TABLE stg_doc_record (
  record_id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  parse_id          BIGINT UNSIGNED NOT NULL COMMENT 'Parse that produced it',
  table_id          BIGINT UNSIGNED NULL COMMENT 'Table it sits in',
  doc_id            BIGINT UNSIGNED NOT NULL COMMENT 'Document',
  page_no           INT UNSIGNED NOT NULL COMMENT 'Page number, 1-based',
  row_index         SMALLINT UNSIGNED NULL COMMENT 'Row in the table (column for wide maturity ladders), 1-based',
  record_kind       ENUM({RECORD_KINDS}) NOT NULL COMMENT 'What one row describes',
  label             VARCHAR(512) NOT NULL COMMENT 'Row label as printed, footnote markers removed (property, tenant, project, year)',
  fields            JSON NOT NULL COMMENT 'Typed fields read from the row (names in reference/data/record_fields.csv); unmapped columns under extra',
  as_of_date        DATE NULL COMMENT 'Date the table describes',
  bbox              JSON NULL COMMENT 'Row position on the page [x0, top, x1, bottom] in PDF points',
  confidence        DECIMAL(4,3) NULL COMMENT 'Parser confidence 0..1',
  quarantined       TINYINT(1) NOT NULL DEFAULT 0 COMMENT '1 = below the confidence bar or failed a hard check; not used by the warehouse',
  quarantine_reason VARCHAR(255) NULL COMMENT 'Why it was quarantined (or kept despite a warning)',
  PRIMARY KEY (record_id),
  KEY ix_parse (parse_id, record_kind),
  KEY ix_doc (doc_id),
  CONSTRAINT fk_rec_parse FOREIGN KEY (parse_id) REFERENCES stg_doc_parse (parse_id)
) {T} COMMENT='Rows of property, tenant, development, debt and lease-expiration tables in documents, as typed records.'""",
    ),
    (
        "wh_property_metric",
        f"""
CREATE TABLE wh_property_metric (
  property_metric_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  company_id        INT UNSIGNED NOT NULL COMMENT 'Reporting company',
  property_id       BIGINT UNSIGNED NULL COMMENT 'Property master row once matched; NULL until the property is matched',
  property_key      CHAR(64) NOT NULL COMMENT 'Hash of normalized name, city and state, for matching',
  property_name     VARCHAR(255) NOT NULL COMMENT 'Property name as printed',
  city              VARCHAR(128) NULL COMMENT 'City as printed',
  state             VARCHAR(64) NULL COMMENT 'State as printed',
  market            VARCHAR(128) NULL COMMENT 'Market or MSA as printed',
  property_type     VARCHAR(64) NULL COMMENT 'Property type as printed',
  metric_id         SMALLINT UNSIGNED NOT NULL COMMENT 'Metric (grain = property)',
  as_of_date        DATE NOT NULL COMMENT 'Date or period end the figure describes (valid time)',
  value             DECIMAL(28,6) NOT NULL COMMENT 'Value in unit',
  unit              VARCHAR(32) NOT NULL COMMENT 'Unit, e.g. USD, sf, pct, count',
{LINEAGE}
  PRIMARY KEY (property_metric_id),
  UNIQUE KEY uq_hash_live (row_hash, is_live),
  KEY ix_company (company_id, as_of_date),
  KEY ix_property (property_id, metric_id, as_of_date),
  KEY ix_key (company_id, property_key),
  CONSTRAINT fk_pm_company FOREIGN KEY (company_id) REFERENCES ref_company (company_id),
  CONSTRAINT fk_pm_property FOREIGN KEY (property_id) REFERENCES wh_property (property_id),
  CONSTRAINT fk_pm_metric FOREIGN KEY (metric_id) REFERENCES ref_metric (metric_id)
) {T} COMMENT='Per-property figures from supplementals (size, occupancy, rent, NOI, RevPAR ...), bitemporal, with lineage to page and row.'""",
    ),
    (
        "wh_lease_expiration",
        f"""
CREATE TABLE wh_lease_expiration (
  lease_exp_id      BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  company_id        INT UNSIGNED NOT NULL COMMENT 'Company',
  as_of_date        DATE NOT NULL COMMENT 'Date of the schedule',
  bucket_label      VARCHAR(64) NOT NULL COMMENT 'Bucket as printed: a year, Thereafter, Month-to-month, Vacant, Total',
  expiry_year       SMALLINT NULL COMMENT 'Year, when the bucket is one year',
  segment           VARCHAR(128) NOT NULL DEFAULT 'Total' COMMENT 'Sub-heading the row sat under, or Total',
  n_leases          INT NULL COMMENT 'Leases expiring',
  n_properties      INT NULL COMMENT 'Properties with leases expiring',
  square_feet       DECIMAL(16,2) NULL COMMENT 'Square feet expiring',
  annualized_rent   DECIMAL(20,2) NULL COMMENT 'Annualized rent expiring, USD',
  rent_pct          DECIMAL(9,4) NULL COMMENT 'Percent of total annualized rent',
  rent_psf          DECIMAL(12,4) NULL COMMENT 'Annualized rent per square foot, USD',
  is_total          TINYINT(1) NOT NULL DEFAULT 0 COMMENT '1 for the total row',
{LINEAGE}
  PRIMARY KEY (lease_exp_id),
  UNIQUE KEY uq_hash_live (row_hash, is_live),
  KEY ix_company (company_id, as_of_date),
  CONSTRAINT fk_le_company FOREIGN KEY (company_id) REFERENCES ref_company (company_id)
) {T} COMMENT='Lease expiration schedules from supplementals.'""",
    ),
    (
        "wh_tenant_exposure",
        f"""
CREATE TABLE wh_tenant_exposure (
  tenant_exp_id     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  company_id        INT UNSIGNED NOT NULL COMMENT 'Company (landlord)',
  as_of_date        DATE NOT NULL COMMENT 'Date of the list',
  tenant_name       VARCHAR(255) NOT NULL COMMENT 'Tenant, customer or operator as printed',
  tenant_rank       SMALLINT NULL COMMENT 'Rank as printed',
  industry          VARCHAR(128) NULL COMMENT 'Industry or line of trade as printed',
  credit_rating     VARCHAR(32) NULL COMMENT 'Credit rating as printed',
  n_properties      INT NULL COMMENT 'Properties or locations',
  n_leases          INT NULL COMMENT 'Leases',
  square_feet       DECIMAL(16,2) NULL COMMENT 'Square feet leased',
  annualized_rent   DECIMAL(20,2) NULL COMMENT 'Annualized rent, USD',
  rent_pct          DECIMAL(9,4) NULL COMMENT 'Percent of total annualized rent (or of the measure the table states)',
  wale_years        DECIMAL(8,2) NULL COMMENT 'Weighted average remaining lease term, years',
  is_total          TINYINT(1) NOT NULL DEFAULT 0 COMMENT '1 for a total row such as Top 20 total',
{LINEAGE}
  PRIMARY KEY (tenant_exp_id),
  UNIQUE KEY uq_hash_live (row_hash, is_live),
  KEY ix_company (company_id, as_of_date),
  KEY ix_tenant (tenant_name),
  CONSTRAINT fk_te_company FOREIGN KEY (company_id) REFERENCES ref_company (company_id)
) {T} COMMENT='Top tenant, customer and operator concentration from supplementals.'""",
    ),
    (
        "wh_development_project",
        f"""
CREATE TABLE wh_development_project (
  dev_project_id    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  company_id        INT UNSIGNED NOT NULL COMMENT 'Company',
  as_of_date        DATE NOT NULL COMMENT 'Date of the schedule',
  project_name      VARCHAR(255) NOT NULL COMMENT 'Project, region or bucket as printed',
  segment           VARCHAR(128) NOT NULL DEFAULT 'Total' COMMENT 'Sub-heading the row sat under (e.g. Under construction, Completed), or Total',
  market            VARCHAR(128) NULL COMMENT 'Market as printed',
  property_type     VARCHAR(64) NULL COMMENT 'Property type as printed',
  units             INT NULL COMMENT 'Units, homes, beds, keys or MW',
  square_feet       DECIMAL(16,2) NULL COMMENT 'Square feet',
  total_cost        DECIMAL(20,2) NULL COMMENT 'Estimated total cost or investment, USD',
  cost_to_date      DECIMAL(20,2) NULL COMMENT 'Cost incurred to date, USD',
  remaining_cost    DECIMAL(20,2) NULL COMMENT 'Cost remaining to fund, USD',
  pct_leased        DECIMAL(9,4) NULL COMMENT 'Percent leased or pre-leased',
  stabilized_yield  DECIMAL(9,4) NULL COMMENT 'Expected stabilized yield, percent',
  ownership_pct     DECIMAL(9,4) NULL COMMENT 'Company ownership share, percent',
  completion_text   VARCHAR(64) NULL COMMENT 'Expected completion or stabilization as printed',
  is_total          TINYINT(1) NOT NULL DEFAULT 0 COMMENT '1 for a total row',
{LINEAGE}
  PRIMARY KEY (dev_project_id),
  UNIQUE KEY uq_hash_live (row_hash, is_live),
  KEY ix_company (company_id, as_of_date),
  CONSTRAINT fk_dp_company FOREIGN KEY (company_id) REFERENCES ref_company (company_id)
) {T} COMMENT='Development and redevelopment pipelines from supplementals.'""",
    ),
    (
        "wh_debt_instrument",
        f"""
CREATE TABLE wh_debt_instrument (
  instrument_id     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  company_id        INT UNSIGNED NOT NULL COMMENT 'Borrower',
  as_of_date        DATE NOT NULL COMMENT 'Date of the schedule',
  instrument        VARCHAR(255) NOT NULL COMMENT 'Loan or note as printed, e.g. 3.600% notes due 2026',
  segment           VARCHAR(128) NOT NULL DEFAULT 'Total' COMMENT 'Sub-heading, e.g. Unsecured, Mortgages',
  debt_type         VARCHAR(64) NULL COMMENT 'Type column as printed',
  principal         DECIMAL(20,2) NULL COMMENT 'Principal outstanding, USD',
  rate              DECIMAL(9,4) NULL COMMENT 'Stated interest rate, percent',
  effective_rate    DECIMAL(9,4) NULL COMMENT 'Effective interest rate, percent',
  maturity_text     VARCHAR(64) NULL COMMENT 'Maturity as printed',
  maturity_year     SMALLINT NULL COMMENT 'Maturity year read from the text',
  is_total          TINYINT(1) NOT NULL DEFAULT 0 COMMENT '1 for a total row',
{LINEAGE}
  PRIMARY KEY (instrument_id),
  UNIQUE KEY uq_hash_live (row_hash, is_live),
  KEY ix_company (company_id, as_of_date),
  CONSTRAINT fk_di_company FOREIGN KEY (company_id) REFERENCES ref_company (company_id)
) {T} COMMENT='Individual loans and notes from supplemental debt schedules (input to debt mark-to-market in NAV).'""",
    ),
]

ALTERS: list[tuple[str, str]] = [
    (
        "ref_metric",
        """
ALTER TABLE ref_metric
  MODIFY COLUMN unit_type ENUM('USD','USD_PER_SHARE','PCT','SF','COUNT','SHARES','RATIO','YEARS','USD_PER_SF','USD_PER_UNIT','MW') NOT NULL COMMENT 'Unit family (USD_PER_UNIT: per room, unit, bed or occupied room per night or month)',
  ADD COLUMN grain ENUM('company','segment','property') NOT NULL DEFAULT 'company' COMMENT 'Level the metric describes: company (incl. segments of it) or a single property' AFTER period_nature""",
    ),
    (
        "ref_metric_map",
        """
ALTER TABLE ref_metric_map
  ADD COLUMN col_role ENUM('value','change','share','any') NOT NULL DEFAULT 'value' COMMENT 'Supplemental rules: which column role the rule reads (change = % change columns)' AFTER segment_regex,
  ADD COLUMN table_kind_regex VARCHAR(255) NULL COMMENT 'Supplemental rules: only tables whose kind matches, e.g. ^(same_store|operating)$' AFTER col_role""",
    ),
    (
        "stg_doc_parse",
        """
ALTER TABLE stg_doc_parse
  ADD COLUMN n_records INT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'Records extracted' AFTER n_items,
  ADD COLUMN n_quarantined INT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'Items and records below the confidence bar' AFTER n_records,
  ADD COLUMN config_hash CHAR(64) NULL COMMENT 'Hash of the overrides and settings the parse ran with (with parser_version, reproduces the parse)' AFTER parser_version""",
    ),
    (
        "stg_doc_table",
        """
ALTER TABLE stg_doc_table
  ADD COLUMN table_kind   VARCHAR(32) NULL COMMENT 'Kind from reference/data/table_kinds.csv, e.g. ffo, same_store, debt_maturity' AFTER section,
  ADD COLUMN orientation  ENUM('series','segments','records') NULL COMMENT 'series: periods across; segments: metrics across, segments down; records: one row per thing' AFTER table_kind,
  ADD COLUMN kind_score   DECIMAL(4,3) NULL COMMENT 'How well the title and labels matched the kind, 0..1' AFTER orientation,
  ADD COLUMN scale_text   VARCHAR(128) NULL COMMENT 'Scale note as printed, e.g. dollars in thousands' AFTER kind_score,
  ADD COLUMN scale_factor DECIMAL(20,0) NULL COMMENT 'Multiplier applied to money values (1000 for thousands)' AFTER scale_text,
  ADD COLUMN scale_source ENUM('table','page','document','override','none') NULL COMMENT 'Where the scale came from: the table, its page, a document-wide statement, an override, or none' AFTER scale_factor,
  ADD COLUMN headers      JSON NULL COMMENT 'Column headers as read' AFTER scale_source,
  ADD COLUMN n_rows       SMALLINT UNSIGNED NULL COMMENT 'Rows read' AFTER headers,
  ADD COLUMN n_cols       SMALLINT UNSIGNED NULL COMMENT 'Value columns read' AFTER n_rows,
  ADD COLUMN total_check  ENUM('pass','fail','mixed') NULL COMMENT 'Total rows equal the rows above them: pass, fail, mixed; NULL = no totals' AFTER n_cols,
  ADD COLUMN as_of_date   DATE NULL COMMENT 'Date the table describes' AFTER total_check""",
    ),
    (
        "stg_doc_line_item",
        """
ALTER TABLE stg_doc_line_item
  ADD COLUMN metric_label      VARCHAR(512) NULL COMMENT 'Label naming the metric: row label (series tables) or column header (segment tables); rules match this' AFTER col_label,
  ADD COLUMN group_label       VARCHAR(255) NULL COMMENT 'Sub-heading the row sits under' AFTER segment_label,
  ADD COLUMN col_role          ENUM('value','change','share') NOT NULL DEFAULT 'value' COMMENT 'value, change (e.g. % change) or share (e.g. % of total) column' AFTER group_label,
  ADD COLUMN period_type       ENUM('Q','YTD','FY','TTM','INSTANT') NULL COMMENT 'Period type read from the header' AFTER period_end,
  ADD COLUMN bbox              JSON NULL COMMENT 'Cell position on the page [x0, top, x1, bottom] in PDF points' AFTER col_role,
  ADD COLUMN quarantined       TINYINT(1) NOT NULL DEFAULT 0 COMMENT '1 = below the confidence bar or failed a hard check; not used by the warehouse' AFTER confidence,
  ADD COLUMN quarantine_reason VARCHAR(255) NULL COMMENT 'Why it was quarantined (or kept despite a warning)' AFTER quarantined""",
    ),
    (
        "wh_debt_maturity",
        """
ALTER TABLE wh_debt_maturity
  MODIFY COLUMN maturity_year SMALLINT NULL COMMENT 'Year the debt matures; NULL for Thereafter and Total buckets',
  ADD COLUMN bucket_label VARCHAR(64) NULL COMMENT 'Bucket as printed: a year, Thereafter, Total' AFTER maturity_year,
  ADD COLUMN pct_of_total DECIMAL(9,4) NULL COMMENT 'Percent of total debt maturing in the bucket' AFTER wtd_avg_rate,
  ADD COLUMN is_total TINYINT(1) NOT NULL DEFAULT 0 COMMENT '1 for the total row' AFTER pct_of_total,
  ADD COLUMN known_to DATE NULL COMMENT 'Knowledge time: date a later document gave a different value; NULL = still the latest' AFTER known_from,
  ADD COLUMN version SMALLINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '1 = first reported, 2+ = later revisions' AFTER known_to,
  ADD COLUMN confidence DECIMAL(4,3) NULL COMMENT 'Parser confidence 0..1 of the source record' AFTER version,
  ADD COLUMN source_sha256 CHAR(64) NULL COMMENT 'Raw object the value was read from' AFTER source_doc_id,
  ADD COLUMN source_table_id BIGINT UNSIGNED NULL COMMENT 'stg_doc_table row' AFTER source_page_no,
  ADD COLUMN source_record_id BIGINT UNSIGNED NULL COMMENT 'stg_doc_record row' AFTER source_table_id,
  ADD COLUMN row_hash CHAR(64) NULL COMMENT 'Hash of key, knowledge time, values and lineage' AFTER source_record_id,
  ADD COLUMN recorded_run_id BIGINT UNSIGNED NULL COMMENT 'Build run that inserted it' AFTER recorded_at,
  ADD COLUMN retired_run_id BIGINT UNSIGNED NULL COMMENT 'Build run that retired it' AFTER retired_at,
  ADD COLUMN is_live TINYINT AS (IF(retired_at IS NULL, 1, NULL)) STORED COMMENT '1 while live, NULL once retired' AFTER retired_run_id,
  ADD UNIQUE KEY uq_hash_live (row_hash, is_live)""",
    ),
]

DROPS = {
    "ref_metric": "ALTER TABLE ref_metric DROP COLUMN grain, MODIFY COLUMN unit_type "
    "ENUM('USD','USD_PER_SHARE','PCT','SF','COUNT','SHARES','RATIO','YEARS','USD_PER_SF') NOT NULL COMMENT 'Unit family'",
    "ref_metric_map": "ALTER TABLE ref_metric_map DROP COLUMN col_role, DROP COLUMN table_kind_regex",
    "stg_doc_parse": "ALTER TABLE stg_doc_parse DROP COLUMN n_records, DROP COLUMN n_quarantined, DROP COLUMN config_hash",
    "stg_doc_table": "ALTER TABLE stg_doc_table DROP COLUMN table_kind, DROP COLUMN orientation, DROP COLUMN kind_score, "
    "DROP COLUMN scale_text, DROP COLUMN scale_factor, DROP COLUMN scale_source, DROP COLUMN headers, DROP COLUMN n_rows, "
    "DROP COLUMN n_cols, DROP COLUMN total_check, DROP COLUMN as_of_date",
    "stg_doc_line_item": "ALTER TABLE stg_doc_line_item DROP COLUMN metric_label, DROP COLUMN group_label, "
    "DROP COLUMN col_role, DROP COLUMN period_type, DROP COLUMN bbox, DROP COLUMN quarantined, DROP COLUMN quarantine_reason",
    "wh_debt_maturity": "DELETE FROM wh_debt_maturity WHERE maturity_year IS NULL; "
    "ALTER TABLE wh_debt_maturity MODIFY COLUMN maturity_year SMALLINT NOT NULL "
    "COMMENT 'Year the debt matures (or first year of a Thereafter bucket)', DROP INDEX uq_hash_live, DROP COLUMN is_live, DROP COLUMN bucket_label, "
    "DROP COLUMN pct_of_total, DROP COLUMN is_total, DROP COLUMN known_to, DROP COLUMN version, DROP COLUMN confidence, "
    "DROP COLUMN source_sha256, DROP COLUMN source_table_id, DROP COLUMN source_record_id, DROP COLUMN row_hash, "
    "DROP COLUMN recorded_run_id, DROP COLUMN retired_run_id",
}

# Latest live, current-knowledge rows of each record table, for the latest schedule date per company.
_LATEST = "x.retired_at IS NULL AND x.known_to IS NULL AND x.as_of_date = (SELECT MAX(y.as_of_date) FROM {t} y WHERE y.company_id = x.company_id AND y.retired_at IS NULL)"

VIEWS: list[tuple[str, str]] = [
    (
        "dl_metric_catalog",
        """
CREATE OR REPLACE SQL SECURITY INVOKER VIEW dl_metric_catalog AS
SELECT s.sector_code, s.name AS sector_name, m.metric_code, m.name AS metric_name, m.category, m.unit_type,
       m.period_nature, m.grain, m.is_non_gaap, m.is_nareit_defined, ms.relevance, ms.sector_code AS defined_for,
       m.definition
FROM ref_sector s
JOIN ref_metric_sector ms ON ms.sector_code IN (s.sector_code, s.parent_code, 'all')
JOIN ref_metric m ON m.metric_id = ms.metric_id AND m.is_active = 1
WHERE s.parent_code IN ('equity', 'mortgage')""",
    ),
    (
        "dl_company_profile",
        """
CREATE OR REPLACE SQL SECURITY INVOKER VIEW dl_company_profile AS
SELECT c.ticker, c.name AS company_name, cm.metric_code, cm.metric_name, cm.category, cm.grain, cm.relevance,
       cm.unit_type, v.period_type, v.period_end, v.segment, v.value, v.unit, v.source_type,
       v.known_from AS published_date, d.source_url AS source_document_url, v.source_page_no,
       (v.value_id IS NOT NULL) AS has_value
FROM ref_company c
JOIN (
    SELECT cs.company_id, m.metric_id, m.metric_code, m.name AS metric_name, m.category, m.grain, m.unit_type,
           MIN(ms.relevance) AS relevance
    FROM ref_company_sector cs
    JOIN ref_sector s ON s.sector_code = cs.sector_code
    JOIN ref_metric_sector ms ON ms.sector_code IN (s.sector_code, s.parent_code, 'all')
    JOIN ref_metric m ON m.metric_id = ms.metric_id AND m.is_active = 1 AND m.grain <> 'property'
    GROUP BY cs.company_id, m.metric_id, m.metric_code, m.name, m.category, m.grain, m.unit_type
) cm ON cm.company_id = c.company_id
LEFT JOIN (
    SELECT l.*, ROW_NUMBER() OVER (PARTITION BY l.company_id, l.metric_id
                                   ORDER BY l.period_end DESC, FIELD(l.period_type, 'Q', 'INSTANT', 'TTM', 'YTD', 'FY'),
                                            (l.segment = 'Total') DESC, l.is_preferred DESC) AS rn
    FROM wh_metric_latest l
) v ON v.company_id = c.company_id AND v.metric_id = cm.metric_id AND v.rn = 1
LEFT JOIN stg_document d ON d.doc_id = v.source_doc_id""",
    ),
    (
        "dl_property_metrics",
        """
CREATE OR REPLACE SQL SECURITY INVOKER VIEW dl_property_metrics AS
SELECT c.ticker, x.property_id, x.property_name, x.city, x.state, x.market, x.property_type, m.metric_code,
       m.name AS metric_name, x.as_of_date, x.value, x.unit, x.known_from AS published_date, x.confidence,
       d.source_url AS source_document_url, x.source_page_no, x.property_metric_id
FROM wh_property_metric x
JOIN ref_company c ON c.company_id = x.company_id
JOIN ref_metric m ON m.metric_id = x.metric_id
JOIN stg_document d ON d.doc_id = x.source_doc_id
WHERE x.retired_at IS NULL AND x.known_to IS NULL""",
    ),
    (
        "dl_debt_maturities",
        f"""
CREATE OR REPLACE SQL SECURITY INVOKER VIEW dl_debt_maturities AS
SELECT c.ticker, x.as_of_date, x.maturity_year, x.bucket_label, x.debt_type, x.amount, x.wtd_avg_rate, x.pct_of_total,
       x.is_total, x.known_from AS published_date, d.source_url AS source_document_url, x.source_page_no
FROM wh_debt_maturity x
JOIN ref_company c ON c.company_id = x.company_id
JOIN stg_document d ON d.doc_id = x.source_doc_id
WHERE {_LATEST.format(t="wh_debt_maturity")}""",
    ),
    (
        "dl_lease_expirations",
        f"""
CREATE OR REPLACE SQL SECURITY INVOKER VIEW dl_lease_expirations AS
SELECT c.ticker, x.as_of_date, x.segment, x.bucket_label, x.expiry_year, x.n_leases, x.n_properties, x.square_feet,
       x.annualized_rent, x.rent_pct, x.rent_psf, x.is_total, x.known_from AS published_date,
       d.source_url AS source_document_url, x.source_page_no
FROM wh_lease_expiration x
JOIN ref_company c ON c.company_id = x.company_id
JOIN stg_document d ON d.doc_id = x.source_doc_id
WHERE {_LATEST.format(t="wh_lease_expiration")}""",
    ),
    (
        "dl_top_tenants",
        f"""
CREATE OR REPLACE SQL SECURITY INVOKER VIEW dl_top_tenants AS
SELECT c.ticker, x.as_of_date, x.tenant_rank, x.tenant_name, x.industry, x.credit_rating, x.n_properties, x.n_leases,
       x.square_feet, x.annualized_rent, x.rent_pct, x.wale_years, x.is_total, x.known_from AS published_date,
       d.source_url AS source_document_url, x.source_page_no
FROM wh_tenant_exposure x
JOIN ref_company c ON c.company_id = x.company_id
JOIN stg_document d ON d.doc_id = x.source_doc_id
WHERE {_LATEST.format(t="wh_tenant_exposure")}""",
    ),
    (
        "dl_development_pipeline",
        f"""
CREATE OR REPLACE SQL SECURITY INVOKER VIEW dl_development_pipeline AS
SELECT c.ticker, x.as_of_date, x.segment, x.project_name, x.market, x.property_type, x.units, x.square_feet,
       x.total_cost, x.cost_to_date, x.remaining_cost, x.pct_leased, x.stabilized_yield, x.ownership_pct,
       x.completion_text, x.is_total, x.known_from AS published_date, d.source_url AS source_document_url,
       x.source_page_no
FROM wh_development_project x
JOIN ref_company c ON c.company_id = x.company_id
JOIN stg_document d ON d.doc_id = x.source_doc_id
WHERE {_LATEST.format(t="wh_development_project")}""",
    ),
    (
        "dl_debt_instruments",
        f"""
CREATE OR REPLACE SQL SECURITY INVOKER VIEW dl_debt_instruments AS
SELECT c.ticker, x.as_of_date, x.segment, x.instrument, x.debt_type, x.principal, x.rate, x.effective_rate,
       x.maturity_text, x.maturity_year, x.is_total, x.known_from AS published_date,
       d.source_url AS source_document_url, x.source_page_no
FROM wh_debt_instrument x
JOIN ref_company c ON c.company_id = x.company_id
JOIN stg_document d ON d.doc_id = x.source_doc_id
WHERE {_LATEST.format(t="wh_debt_instrument")}""",
    ),
    (
        "ops_parse_quarantine",
        """
CREATE OR REPLACE SQL SECURITY INVOKER VIEW ops_parse_quarantine AS
SELECT 'item' AS kind, i.item_id AS id, d.doc_id, c.ticker, d.period_end_date, i.page_no, t.title AS table_title,
       t.table_kind, i.row_label AS label, i.col_label AS detail, i.raw_text, i.confidence, i.quarantine_reason
FROM stg_document d
JOIN stg_doc_line_item i ON i.parse_id = d.current_parse_id AND i.quarantined = 1
LEFT JOIN stg_doc_table t ON t.table_id = i.table_id
LEFT JOIN ref_company c ON c.company_id = d.company_id
UNION ALL
SELECT 'record', r.record_id, d.doc_id, c.ticker, d.period_end_date, r.page_no, t.title, r.record_kind, r.label,
       NULL, NULL, r.confidence, r.quarantine_reason
FROM stg_document d
JOIN stg_doc_record r ON r.parse_id = d.current_parse_id AND r.quarantined = 1
LEFT JOIN stg_doc_table t ON t.table_id = r.table_id
LEFT JOIN ref_company c ON c.company_id = d.company_id""",
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
        for stmt in DROPS[name].split("; "):
            op.execute(stmt)
    for name, _ddl in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {name}")
