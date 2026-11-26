"""Property master: source records, stable property ids, matching, ownership history.

ref_property_type      property type dictionary (sector, default size unit), seeded from CSV
ref_property_type_map  rules mapping reported type text to a type code, seeded from CSV
ref_property_source    source map: which source lists each REIT's properties, seeded from CSV
ref_geocode_cache      geocoder answers per normalized address
stg_property_record    every property row any source gave us, with lineage
wh_property            one row per real property; ids are stable and never reused (recreated:
                       the baseline table was never filled)
wh_property_xref       which property each record was matched to, how, and how sure
wh_property_alias      names, addresses and source references seen for a property
wh_property_ownership  ownership spells by REIT, bitemporal like wh_metric_value
dl_properties          client view: live properties with current owners

See docs/adr/0010-property-master.md.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-26
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

T = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"

SOURCE_KINDS = "'sec_schedule_iii','supplemental','corporate_website','public_dataset','manual'"
UNIT_KINDS = "'sf','units','beds','keys','sites','acres','towers','screens','mw','other'"
INTEREST = "'fee','ground_lease','leasehold','joint_venture','mortgage','unknown'"

# The baseline wh_property was keyed per company and never filled; it is recreated with a
# global identity. wh_property_snapshot (also never filled) keeps its foreign key to it.
PRE = [
    "ALTER TABLE wh_property_snapshot DROP FOREIGN KEY fk_snap_prop",
    "DROP TABLE wh_property",
]

TABLES: list[tuple[str, str]] = [
    (
        "ref_property_type",
        f"""
CREATE TABLE ref_property_type (
  type_code          VARCHAR(32) NOT NULL COMMENT 'Stable type code, e.g. apartment, senior_housing, net_lease_retail',
  sector             VARCHAR(32) NOT NULL COMMENT 'Sector the type rolls up to, e.g. residential, healthcare, retail',
  name               VARCHAR(64) NOT NULL COMMENT 'Display name',
  default_unit_kind  ENUM({UNIT_KINDS}) NULL COMMENT 'Usual size unit for the type (sf, units, beds, keys, sites, acres...)',
  description        VARCHAR(512) NULL COMMENT 'What belongs in the type',
  updated_at         DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'Last change (UTC)',
  PRIMARY KEY (type_code)
) {T} COMMENT='Property types and their sectors. Seeded from reference/data/property_types.csv.'""",
    ),
    (
        "ref_property_type_map",
        f"""
CREATE TABLE ref_property_type_map (
  rule_id        SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  rule_key       VARCHAR(64) NOT NULL COMMENT 'Stable key from the seed file',
  pattern        VARCHAR(255) NOT NULL COMMENT 'Case-insensitive regular expression on the reported type, name or sector text',
  type_code      VARCHAR(32) NOT NULL COMMENT 'Type assigned when the pattern matches',
  priority       SMALLINT NOT NULL DEFAULT 100 COMMENT 'Lower runs first; first match wins',
  applies_to     ENUM('type','name','company_sector') NOT NULL DEFAULT 'type' COMMENT 'Which text the pattern is tested against',
  notes          VARCHAR(255) NULL COMMENT 'Why the rule exists',
  PRIMARY KEY (rule_id),
  UNIQUE KEY uq_rule_key (rule_key),
  CONSTRAINT fk_ptm_type FOREIGN KEY (type_code) REFERENCES ref_property_type (type_code)
) {T} COMMENT='Rules mapping reported property type text to ref_property_type. Seeded from reference/data/property_type_map.csv.'""",
    ),
    (
        "ref_property_source",
        f"""
CREATE TABLE ref_property_source (
  source_row_id          INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  company_id             INT UNSIGNED NOT NULL COMMENT 'Company',
  source_kind            ENUM({SOURCE_KINDS}) NOT NULL COMMENT 'Kind of source',
  lists_properties       ENUM('yes','partial','no','unknown') NOT NULL DEFAULT 'unknown' COMMENT 'yes: one row per property; partial: some properties or segments only; no: not at property level',
  status                 ENUM('active','candidate','not_available','blocked','unknown') NOT NULL DEFAULT 'candidate' COMMENT 'active: a loader runs it; candidate: found, no loader yet; not_available: source does not list properties; blocked: site refuses us',
  list_url               VARCHAR(2048) NULL COMMENT 'Page or file that lists the properties (website and dataset sources)',
  list_url_hash          CHAR(64) AS (SHA2(COALESCE(list_url, ''), 256)) STORED COMMENT 'Hash of list_url, for uniqueness',
  list_format            ENUM('xbrl','html_table','html_cards','json','csv','xlsx','pdf','map_only','unknown') NOT NULL DEFAULT 'unknown' COMMENT 'How the list is published (html_table, json, csv and xlsx have a loader)',
  records_path           VARCHAR(255) NULL COMMENT 'JSON: dotted path to the list; HTML: table index (default the largest); XLSX: sheet name',
  field_map              VARCHAR(1024) NULL COMMENT 'JSON object: record field to source column; empty = guess from headers',
  detail                 VARCHAR(32) NULL COMMENT 'Schedule III detail level: individual, by_market, mixed, unknown',
  approx_property_count  INT UNSIGNED NULL COMMENT 'Property count the company states, for coverage checks',
  count_unit             VARCHAR(32) NULL COMMENT 'Unit of approx_property_count (properties, communities, hotels, towers...)',
  checked_date           DATE NULL COMMENT 'When the source map row was last researched',
  notes                  VARCHAR(1024) NULL COMMENT 'Research notes',
  profile_source         VARCHAR(255) NULL COMMENT 'Seed file and hash this row came from',
  updated_at             DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'Last change (UTC)',
  PRIMARY KEY (source_row_id),
  UNIQUE KEY uq_source (company_id, source_kind, list_url_hash),
  CONSTRAINT fk_psrc_company FOREIGN KEY (company_id) REFERENCES ref_company (company_id)
) {T} COMMENT='Source map: where each REIT lists its properties. Seeded from reference/data/property_sources.csv.'""",
    ),
    (
        "ref_geocode_cache",
        f"""
CREATE TABLE ref_geocode_cache (
  address_key      CHAR(64) NOT NULL COMMENT 'Hash of the normalized address (see properties.normalize.address_key)',
  input_address    VARCHAR(512) NOT NULL COMMENT 'Address sent to the geocoder',
  provider         VARCHAR(32) NOT NULL COMMENT 'Geocoder, e.g. census',
  status           ENUM('match','no_match','tie','error') NOT NULL COMMENT 'Geocoder answer',
  match_type       VARCHAR(16) NULL COMMENT 'Exact or Non_Exact (Census)',
  matched_address  VARCHAR(512) NULL COMMENT 'Address the geocoder matched',
  latitude         DECIMAL(9,6) NULL COMMENT 'Latitude (WGS84)',
  longitude        DECIMAL(9,6) NULL COMMENT 'Longitude (WGS84)',
  precision_level  ENUM('rooftop','street','zip','city','none') NOT NULL DEFAULT 'none' COMMENT 'How precise the point is',
  provider_ref     VARCHAR(64) NULL COMMENT 'Provider id of the matched feature (Census TIGER line id)',
  raw_sha256       CHAR(64) NULL COMMENT 'Raw object with the provider response',
  geocoded_at      DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'UTC time of the answer',
  PRIMARY KEY (address_key),
  KEY ix_status (status)
) {T} COMMENT='Geocoder answers per normalized address, so each address is geocoded once.'""",
    ),
    (
        "stg_property_record",
        f"""
CREATE TABLE stg_property_record (
  record_id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key; supplemental property metrics key to this',
  record_key           CHAR(64) NOT NULL COMMENT 'Hash of company, source, source locator, as-of date and row identity; reloads are idempotent on it',
  company_id           INT UNSIGNED NOT NULL COMMENT 'Company whose list this row came from',
  source_kind          ENUM({SOURCE_KINDS}) NOT NULL COMMENT 'Kind of source',
  source_row_id        INT UNSIGNED NULL COMMENT 'ref_property_source row, when the source is in the source map',
  source_doc_id        BIGINT UNSIGNED NULL COMMENT 'stg_document row (supplementals)',
  source_parse_id      BIGINT UNSIGNED NULL COMMENT 'stg_doc_parse row (supplementals)',
  source_table_id      BIGINT UNSIGNED NULL COMMENT 'stg_doc_table row (supplementals)',
  source_page_no       INT UNSIGNED NULL COMMENT 'Page in the source document',
  source_row_index     INT UNSIGNED NULL COMMENT 'Row position in the source table or list, 1-based',
  source_sha256        CHAR(64) NULL COMMENT 'Raw object the row was read from',
  source_url           VARCHAR(2048) NULL COMMENT 'URL the row was read from',
  accession_no         CHAR(20) NULL COMMENT 'SEC filing (Schedule III)',
  as_of_date           DATE NOT NULL COMMENT 'Date the list describes (balance sheet date, page date for websites)',
  known_from           DATE NOT NULL COMMENT 'Knowledge time: when the market could know it (filing or publication date, or fetch date)',
  source_property_ref  VARCHAR(255) NULL COMMENT 'The source''s own identifier for the row (XBRL member, website id, property code)',
  name                 VARCHAR(255) NULL COMMENT 'Property name as reported',
  address_line         VARCHAR(255) NULL COMMENT 'Street address as reported',
  city                 VARCHAR(128) NULL COMMENT 'City',
  state                VARCHAR(64) NULL COMMENT 'State or province (2-letter for the US when known)',
  postal_code          VARCHAR(16) NULL COMMENT 'Postal code',
  country              CHAR(2) NULL COMMENT 'ISO country code',
  market               VARCHAR(128) NULL COMMENT 'Market, MSA or region as reported',
  raw_property_type    VARCHAR(128) NULL COMMENT 'Property type text as reported',
  type_code            VARCHAR(32) NULL COMMENT 'ref_property_type code from ref_property_type_map rules',
  rentable_sf          DECIMAL(16,2) NULL COMMENT 'Rentable square feet',
  unit_count           DECIMAL(14,2) NULL COMMENT 'Size in unit_kind (apartment units, beds, keys, sites, acres...)',
  unit_kind            ENUM({UNIT_KINDS}) NULL COMMENT 'Unit of unit_count',
  land_acres           DECIMAL(14,3) NULL COMMENT 'Land area in acres',
  year_built           SMALLINT UNSIGNED NULL COMMENT 'Year built (first year when a range is reported)',
  year_renovated       SMALLINT UNSIGNED NULL COMMENT 'Latest renovation year',
  acquired_date        DATE NULL COMMENT 'Acquisition date as reported (first day of the year when only a year is given)',
  acquired_text        VARCHAR(64) NULL COMMENT 'Acquisition date text as printed, e.g. 2004/2016',
  disposed_date        DATE NULL COMMENT 'Disposition date as reported',
  ownership_pct        DECIMAL(7,4) NULL COMMENT 'Company share of the property, percent',
  interest_type        ENUM({INTEREST}) NULL COMMENT 'Fee, ground lease, leasehold, joint venture, mortgage',
  occupancy_pct        DECIMAL(7,4) NULL COMMENT 'Occupancy percent as reported with the list',
  encumbrances         DECIMAL(20,2) NULL COMMENT 'Schedule III: encumbrances, USD',
  initial_cost_land    DECIMAL(20,2) NULL COMMENT 'Schedule III: initial cost of land, USD',
  initial_cost_bldg    DECIMAL(20,2) NULL COMMENT 'Schedule III: initial cost of buildings and improvements, USD',
  costs_capitalized    DECIMAL(20,2) NULL COMMENT 'Schedule III: costs capitalized after acquisition, USD',
  gross_land           DECIMAL(20,2) NULL COMMENT 'Schedule III: gross amount carried, land, USD',
  gross_bldg           DECIMAL(20,2) NULL COMMENT 'Schedule III: gross amount carried, buildings and improvements, USD',
  gross_total          DECIMAL(20,2) NULL COMMENT 'Schedule III: gross amount carried, total, USD',
  accum_depreciation   DECIMAL(20,2) NULL COMMENT 'Schedule III: accumulated depreciation, USD',
  is_aggregate         TINYINT(1) NOT NULL DEFAULT 0 COMMENT '1 when the row stands for several properties (market, type or other totals); never becomes a property',
  aggregate_count      INT UNSIGNED NULL COMMENT 'Properties the aggregate row stands for, when stated',
  latitude             DECIMAL(9,6) NULL COMMENT 'Latitude as reported by the source',
  longitude            DECIMAL(9,6) NULL COMMENT 'Longitude as reported by the source',
  attributes           JSON NULL COMMENT 'Sector-specific attributes as reported (e.g. tenant, brand, operator, care mix, MW)',
  norm_name            VARCHAR(255) NULL COMMENT 'Normalized name used for matching',
  norm_address         VARCHAR(255) NULL COMMENT 'Normalized street address used for matching',
  address_key          CHAR(64) NULL COMMENT 'Hash of normalized street and ZIP (or city and state); NULL without a street address',
  loader               VARCHAR(64) NOT NULL COMMENT 'Loader or parser that produced the row',
  loader_version       VARCHAR(16) NOT NULL COMMENT 'Its version',
  run_id               BIGINT UNSIGNED NULL COMMENT 'Job run that loaded it',
  created_at           DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'System time inserted',
  retired_at           DATETIME(6) NULL COMMENT 'System time a newer load of the same source replaced it; NULL = live',
  is_live              TINYINT AS (IF(retired_at IS NULL, 1, NULL)) STORED COMMENT '1 while live, NULL once retired',
  PRIMARY KEY (record_id),
  UNIQUE KEY uq_record_key (record_key),
  KEY ix_company_asof (company_id, source_kind, as_of_date),
  KEY ix_address_key (address_key),
  KEY ix_source_ref (company_id, source_kind, source_property_ref),
  KEY ix_doc (source_doc_id),
  CONSTRAINT fk_prec_company FOREIGN KEY (company_id) REFERENCES ref_company (company_id),
  CONSTRAINT fk_prec_type FOREIGN KEY (type_code) REFERENCES ref_property_type (type_code),
  CONSTRAINT ck_prec_pct CHECK (ownership_pct IS NULL OR (ownership_pct >= 0 AND ownership_pct <= 100))
) {T} COMMENT='Every property row any source gave us: one per property per source per as-of date, faithful to the source, with lineage.'""",
    ),
    (
        "wh_property",
        f"""
CREATE TABLE wh_property (
  property_id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Stable property id: assigned once, never reused, survives sales between REITs',
  status                   ENUM('active','inactive','merged') NOT NULL DEFAULT 'active' COMMENT 'active: owned by a tracked REIT now; inactive: no current owner in the universe; merged: duplicate, see merged_into_property_id',
  merged_into_property_id  BIGINT UNSIGNED NULL COMMENT 'Survivor when this row was found to duplicate another',
  owner_company_id         INT UNSIGNED NULL COMMENT 'Largest current owner (denormalized from wh_property_ownership)',
  name                     VARCHAR(255) NULL COMMENT 'Canonical name (highest-priority, newest source)',
  address_line             VARCHAR(255) NULL COMMENT 'Canonical street address',
  city                     VARCHAR(128) NULL COMMENT 'City',
  state                    VARCHAR(64) NULL COMMENT 'State or province',
  postal_code              VARCHAR(16) NULL COMMENT 'Postal code',
  country                  CHAR(2) NULL COMMENT 'ISO country code',
  market                   VARCHAR(128) NULL COMMENT 'Market or MSA as reported',
  type_code                VARCHAR(32) NULL COMMENT 'Property type',
  sector                   VARCHAR(32) NULL COMMENT 'Sector of the type',
  rentable_sf              DECIMAL(16,2) NULL COMMENT 'Latest rentable square feet',
  unit_count               DECIMAL(14,2) NULL COMMENT 'Latest size in unit_kind',
  unit_kind                ENUM({UNIT_KINDS}) NULL COMMENT 'Unit of unit_count',
  land_acres               DECIMAL(14,3) NULL COMMENT 'Land area in acres',
  year_built               SMALLINT UNSIGNED NULL COMMENT 'Year built',
  latitude                 DECIMAL(9,6) NULL COMMENT 'Latitude (WGS84)',
  longitude                DECIMAL(9,6) NULL COMMENT 'Longitude (WGS84)',
  geo_precision            ENUM('rooftop','street','zip','city','reported','none') NOT NULL DEFAULT 'none' COMMENT 'Precision of the point',
  geo_source               VARCHAR(32) NULL COMMENT 'Where the point came from: census, source, manual',
  address_key              CHAR(64) NULL COMMENT 'Hash of the canonical normalized address',
  first_seen               DATE NULL COMMENT 'Earliest as-of date of any linked record',
  last_seen                DATE NULL COMMENT 'Latest as-of date of any linked record',
  n_records                INT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'Live records linked to it',
  n_source_kinds           TINYINT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'Distinct source kinds that list it (more = better confirmed)',
  attr_hash                CHAR(64) NULL COMMENT 'Hash of the canonical attributes; a rebuild updates the row only when it changes',
  created_run_id           BIGINT UNSIGNED NULL COMMENT 'Build run that created the id',
  created_at               DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'Row creation (UTC)',
  updated_at               DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'Last change (UTC)',
  PRIMARY KEY (property_id),
  KEY ix_address_key (address_key),
  KEY ix_owner (owner_company_id, status),
  KEY ix_geo (state, city),
  CONSTRAINT fk_prop_merged FOREIGN KEY (merged_into_property_id) REFERENCES wh_property (property_id),
  CONSTRAINT fk_prop_owner FOREIGN KEY (owner_company_id) REFERENCES ref_company (company_id),
  CONSTRAINT fk_prop_type FOREIGN KEY (type_code) REFERENCES ref_property_type (type_code)
) {T} COMMENT='Property master: one row per real property, stable ids, canonical attributes chosen by source priority.'""",
    ),
    (
        "wh_property_xref",
        f"""
CREATE TABLE wh_property_xref (
  xref_id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  record_id      BIGINT UNSIGNED NOT NULL COMMENT 'Source record',
  property_id    BIGINT UNSIGNED NOT NULL COMMENT 'Property it was matched to',
  method         ENUM('source_ref','address','name_city','geo','new','manual') NOT NULL COMMENT 'Rule that made the link (new = no match, a property was created)',
  score          DECIMAL(5,4) NOT NULL COMMENT 'Match strength 0..1',
  review_status  ENUM('auto','needs_review','confirmed','rejected') NOT NULL DEFAULT 'auto' COMMENT 'needs_review: close call, see candidates; confirmed or rejected by a person',
  candidates     JSON NULL COMMENT 'Other properties considered, with scores, for review',
  matcher_version VARCHAR(16) NOT NULL COMMENT 'Matcher version',
  run_id         BIGINT UNSIGNED NULL COMMENT 'Build run',
  matched_at     DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'System time linked',
  retired_at     DATETIME(6) NULL COMMENT 'System time the link was replaced (re-match or merge); NULL = live',
  is_live        TINYINT AS (IF(retired_at IS NULL, 1, NULL)) STORED COMMENT '1 while live, NULL once retired',
  PRIMARY KEY (xref_id),
  UNIQUE KEY uq_record_live (record_id, is_live),
  KEY ix_property (property_id),
  KEY ix_review (review_status),
  CONSTRAINT fk_xref_record FOREIGN KEY (record_id) REFERENCES stg_property_record (record_id),
  CONSTRAINT fk_xref_property FOREIGN KEY (property_id) REFERENCES wh_property (property_id)
) {T} COMMENT='Record to property links with method, score and review status. Manual links survive rebuilds.'""",
    ),
    (
        "wh_property_alias",
        f"""
CREATE TABLE wh_property_alias (
  property_id   BIGINT UNSIGNED NOT NULL COMMENT 'Property',
  alias_type    ENUM('name','address','source_ref') NOT NULL COMMENT 'What the alias is',
  alias_norm    VARCHAR(255) NOT NULL COMMENT 'Normalized alias used for matching',
  alias_hash    CHAR(64) AS (SHA2(alias_norm, 256)) STORED COMMENT 'Hash of alias_norm, for the key',
  alias_value   VARCHAR(512) NOT NULL COMMENT 'Alias as first reported',
  company_id    INT UNSIGNED NULL COMMENT 'Company whose source used it',
  source_kind   ENUM({SOURCE_KINDS}) NOT NULL COMMENT 'Source kind that used it',
  first_seen    DATE NOT NULL COMMENT 'Earliest as-of date it was used',
  last_seen     DATE NOT NULL COMMENT 'Latest as-of date it was used',
  PRIMARY KEY (property_id, alias_type, alias_hash, source_kind),
  KEY ix_alias (alias_type, alias_hash),
  CONSTRAINT fk_alias_property FOREIGN KEY (property_id) REFERENCES wh_property (property_id)
) {T} COMMENT='Every name, address and source reference seen for a property, so later records match it.'""",
    ),
    (
        "wh_property_ownership",
        f"""
CREATE TABLE wh_property_ownership (
  ownership_id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  property_id          BIGINT UNSIGNED NOT NULL COMMENT 'Property',
  company_id           INT UNSIGNED NOT NULL COMMENT 'Owner',
  spell_no             SMALLINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '1 for the first period of ownership, 2+ if the company sold and bought it back',
  ownership_pct        DECIMAL(7,4) NULL COMMENT 'Latest reported share, percent (NULL = not reported, usually 100)',
  interest_type        ENUM({INTEREST}) NULL COMMENT 'Latest reported interest type',
  acquired_date        DATE NULL COMMENT 'Start of ownership',
  acquired_basis       ENUM('reported','first_seen') NOT NULL COMMENT 'reported by a source, or the first as-of date it was listed',
  disposed_date        DATE NULL COMMENT 'End of ownership; NULL = still owned',
  disposed_basis       ENUM('reported','not_listed') NULL COMMENT 'reported by a source, or the first complete list from the same source that no longer had it',
  first_seen_as_of     DATE NOT NULL COMMENT 'Earliest as-of date the company listed it',
  last_seen_as_of      DATE NOT NULL COMMENT 'Latest as-of date the company listed it',
  known_from           DATE NOT NULL COMMENT 'Knowledge time: first publication of the spell',
  last_record_id       BIGINT UNSIGNED NOT NULL COMMENT 'Newest record behind the spell',
  row_hash             CHAR(64) NOT NULL COMMENT 'Hash of the spell; equal hash = identical row',
  recorded_at          DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'System time inserted',
  recorded_run_id      BIGINT UNSIGNED NULL COMMENT 'Build run that inserted it',
  retired_at           DATETIME(6) NULL COMMENT 'System time a rebuild replaced it; NULL = live',
  is_live              TINYINT AS (IF(retired_at IS NULL, 1, NULL)) STORED COMMENT '1 while live, NULL once retired',
  PRIMARY KEY (ownership_id),
  UNIQUE KEY uq_live (property_id, company_id, spell_no, is_live),
  KEY ix_company (company_id, disposed_date),
  CONSTRAINT fk_own_property FOREIGN KEY (property_id) REFERENCES wh_property (property_id),
  CONSTRAINT fk_own_company FOREIGN KEY (company_id) REFERENCES ref_company (company_id),
  CONSTRAINT ck_own_dates CHECK (disposed_date IS NULL OR acquired_date IS NULL OR disposed_date >= acquired_date)
) {T} COMMENT='Ownership spells: which REIT owned which property when, with system time like wh_metric_value.'""",
    ),
]

POST = [
    "ALTER TABLE wh_property_snapshot ADD CONSTRAINT fk_snap_prop FOREIGN KEY (property_id) REFERENCES wh_property (property_id)"
]

VIEWS: list[tuple[str, str]] = [
    (
        "dl_properties",
        """
CREATE OR REPLACE SQL SECURITY INVOKER VIEW dl_properties AS
SELECT p.property_id, p.name, p.address_line, p.city, p.state, p.postal_code, p.country, p.market,
       p.type_code, p.sector, p.rentable_sf, p.unit_count, p.unit_kind, p.land_acres, p.year_built,
       p.latitude, p.longitude, p.geo_precision, p.status,
       c.ticker AS owner_ticker, c.cik AS owner_cik,
       o.ownership_pct, o.interest_type, o.acquired_date, o.acquired_basis,
       p.first_seen, p.last_seen, p.n_source_kinds, p.updated_at
FROM wh_property p
JOIN wh_property_ownership o ON o.property_id = p.property_id AND o.retired_at IS NULL AND o.disposed_date IS NULL
JOIN ref_company c ON c.company_id = o.company_id
WHERE p.status = 'active'""",
    ),
]

RECREATE_OLD_PROPERTY = f"""
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
) {T} COMMENT='Property master, filled by the supplemental parser.'"""


def upgrade() -> None:
    for ddl in PRE:
        op.execute(ddl)
    for _name, ddl in TABLES:
        op.execute(ddl)
    for ddl in POST:
        op.execute(ddl)
    for _name, ddl in VIEWS:
        op.execute(ddl)


def downgrade() -> None:
    for name, _ddl in reversed(VIEWS):
        op.execute(f"DROP VIEW IF EXISTS {name}")
    op.execute("ALTER TABLE wh_property_snapshot DROP FOREIGN KEY fk_snap_prop")
    for name, _ddl in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {name}")
    op.execute(RECREATE_OLD_PROPERTY)
    op.execute(
        "ALTER TABLE wh_property_snapshot ADD CONSTRAINT fk_snap_prop FOREIGN KEY (property_id) "
        "REFERENCES wh_property (property_id)"
    )
