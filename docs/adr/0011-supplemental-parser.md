# ADR 0011: Supplemental PDF parser, record tables and the sector metric catalog

Status: accepted, 2026-11-26

## Context
Supplementals carry what XBRL does not: same-store results, leasing spreads, occupancy,
debt maturity ladders, lease expirations, top tenants, development pipelines, property
lists, and sector figures (RevPAR, rent coverage, NOI per occupied square foot, tenancy
ratios, book value per share). Every REIT lays them out differently, the layout drifts
from quarter to quarter, and which figures matter depends on the property sector. A
client asking "give me all of the relevant information for NNN REIT" expects the net
lease figures, not a hotel's.

Constraints carried over: raw bytes are immutable (ADR 0003), the warehouse is bitemporal
with lineage on every value (ADR 0004), mappings are data (ADR 0005). The property master
(`wh_property`, `wh_property_snapshot`) belongs to the property database work (ADR 0010);
this parser does not write it.

## Decision
1. **Positioned words, not text dumps.** `parsers/pdf/` reads each page's words with their
   coordinates (pdfplumber, MIT) and rebuilds tables from alignment: right edges for
   numbers, left edges for labels, stacked header lines merged into one header per
   column ("Three Months Ended September 30," over "2026"). Pure functions after the one
   call that reads the PDF, so a parser change is replayed over stored bytes offline.
2. **Classify, then read.** Each table gets a kind (`ffo`, `same_store`, `debt_maturity`,
   `tenant`, `property`, ...) and an orientation from `reference/data/table_kinds.csv`
   (title and body patterns with weights). Series tables (metrics down the side,
   periods across) become line items; segment tables become items keyed by segment;
   record tables become records whose columns map to fields through
   `reference/data/record_fields.csv`. Both files are data: adding a layout is a CSV
   row and a test, not code.
3. **Every value says where it came from and how sure we are.** Items and records carry
   doc id, file SHA-256, page, table, row, column, the cell's bounding box, the raw
   text, the scale applied and where it came from (table, page, a document-wide
   statement, an override), the period read from the header, and a confidence score.
4. **Quarantine instead of guessing.** Anything under `min_confidence` (0.80), or with a
   hard problem (unreadable number, no period, money with no scale note anywhere, two
   columns under one merged header), is stored with `quarantined = 1` and a reason, and
   never reaches the warehouse. `ops_parse_quarantine` lists it for review. A wrong
   number is worse than a missing one: a 1,000x scale error in a client feed is the
   failure this rule exists to prevent.
5. **Per-company overrides as data.** `reference/data/parse_overrides.csv` (seeded into
   `ref_parse_override`, valid-time bounded) can skip a table, force its kind, fix its
   scale or change the confidence bar for one company, by title pattern and page range.
   The parser's config hash (version plus overrides) is stored on every parse, so it is
   clear which rules produced which numbers.
6. **Record tables in the warehouse.** `wh_debt_maturity` (extended), `wh_debt_instrument`,
   `wh_lease_expiration`, `wh_tenant_exposure`, `wh_development_project` and
   `wh_property_metric` hold one row per schedule line, versioned like `wh_metric_value`
   (known_from / known_to from publication dates, retired rows kept, `row_hash` diffing
   so a rebuild is idempotent) with full lineage to file hash, page, table and record.
7. **Property facts key to the property master, not into it.** `wh_property_metric` keys a
   property by a hash of normalized name, city and state (`property_key`) and fills
   `property_id` when `wh_property` has a row with that key for the company. Matching,
   merging and history of properties stay with ADR 0010.
8. **Sector metric catalog.** `ref_sector` (37 sectors under equity and mortgage),
   `ref_company_sector` (from the universe's sector label through
   `sector_labels.csv`, manual rows kept), and `ref_metric_sector` (each metric's
   relevance per sector: core, common, optional) decide what is relevant for a company.
   110 metrics are defined, 55 of them new and supplemental-only, with a `grain` of
   company, segment or property. Metrics with `grain = property` live only in
   `wh_property_metric`.
9. **One call for everything on a company.** `dl_company_profile` lists every relevant
   metric for a company's sectors with its latest value, source and page, or marks it
   missing; `dl_debt_maturities`, `dl_debt_instruments`, `dl_lease_expirations`,
   `dl_top_tenants`, `dl_development_pipeline` and `dl_property_metrics` give the latest
   schedules. `reitstreet company-profile --ticker NNN` and `export` use these views.
10. **Measured, not assumed.** `reitstreet parse-eval FOLDER` runs the parser over a folder
    of PDFs with no database and reports tables found, values read, quarantine rate and
    core-metric coverage by sector. `parse-coverage` gives the same per company from the
    warehouse. New DQ checks: record lineage, one current version per record key,
    percentages in range, lease shares summing to about 100%, property count swings,
    debt ladder against total debt, parse quarantine rate, companies without a sector.

## Consequences
- Coverage grows by adding rows to CSVs (table kinds, record fields, mapping rules,
  overrides) and replaying, with no schema change.
- Values in quarantine are not lost; raising a bar or adding an override and replaying
  promotes them, with the change visible in `stg_doc_parse.config_hash`.
- Scanned (image-only) PDFs produce no words and end in `needs_review`; OCR is out of
  scope for this decision.
- Guidance ranges (low, high) are read as items but not yet mapped to metrics; that needs
  its own period semantics (a forecast is not a fact) and will get its own ADR.
- `wh_property_metric.property_id` changes when the property master learns a new match;
  the row is retired and reinserted like any other change, so history shows when the
  link was made.
- The migration is revision 0005 after 0003. If the property master migration (0004)
  merges first, 0005's `down_revision` moves to 0004; the two touch different tables.
