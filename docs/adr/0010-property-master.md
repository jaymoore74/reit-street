# ADR 0010: Property master

Status: accepted, 2026-11-26

## Context
NAV models, sector screens and every property-level number need one answer to "which
properties does this REIT own, where, and how big are they", kept over time. No single
public source gives it:

| Source | What it gives | Gaps |
|---|---|---|
| 10-K Schedule III | Every equity REIT files it. Often one row per property with land, building, encumbrances, accumulated depreciation, year built and date acquired; tagged in XBRL since 2019 | Many REITs aggregate by market or type; locations are often only "Dallas, TX"; annual |
| Supplementals | Quarterly; property-level occupancy, size, rent, NOI for many sectors | Layout differs by company and over time; some list nothing below the market level |
| Company websites | Leasing and portfolio pages (communities, centers, hotels, store locators) with addresses | No history; layouts change; some are maps only |
| Public datasets | FCC tower registrations, CMS skilled nursing data and others, by sector | Owner names need matching to the REIT; coverage varies |
| Manual lists | Analyst and archive lists | Point in time |

The same building also changes hands between REITs, gets renamed, and appears with
slightly different names and addresses in each source.

## Decision
1. **Sources stay sources.** Every property row any source gives us is a
   `stg_property_record`: one row per property per source per as-of date, faithful to
   the source, with full lineage (document, page, table, row, raw object, loader and
   version). Reloads are idempotent on a content key; a loader change retires old rows.
2. **One stable id per real property.** `wh_property.property_id` is assigned once and
   never reused or renumbered. A property sold from one REIT to another keeps its id.
   Duplicates found later are merged by pointing `merged_into_property_id` at the
   survivor; nothing is deleted.
3. **Matching is explicit and reviewable.** `wh_property_xref` links each record to a
   property with the method (source reference, address, name in city, geocode, new,
   manual), a score and a review status. Rules run strongest first: the same source's
   own reference, normalized street address plus ZIP, name similarity within the same
   company and city, then distance. Close calls go to `needs_review` with the candidates
   stored. Two rows from the same document never collapse into one property
   automatically. Manual links are never touched by a rebuild.
4. **Ownership is history, not a column.** `wh_property_ownership` holds spells
   (company, share, interest type, acquired, disposed) with knowledge time and system
   time like metrics (ADR 0004). Acquired and disposed dates are reported when a source
   gives them and otherwise inferred from first and last sighting, flagged as such.
5. **Canonical attributes follow source priority**: manual, supplemental, company
   website, Schedule III, public dataset, newest first within a source. Aliases
   (names, addresses, source references) are kept in `wh_property_alias` so future
   matches find them.
6. **The source map is data.** `reference/data/property_sources.csv` (loaded into
   `ref_property_source`) records for each REIT which sources list its properties, the
   URL and format, and a status. Coverage is a report, not a guess.
7. **Sector differences live in a type table and JSON**, not new columns per sector:
   `ref_property_type` (seeded from CSV) gives each type its sector and default size unit
   (sf, units, beds, keys, sites, acres); sector-specific attributes travel in
   `attributes` JSON on the record.
8. **Geocoding is cached and attributed.** `ref_geocode_cache` keys on the normalized
   address; the provider response is kept in the raw store. The default provider is the
   free US Census batch geocoder.
9. **Property-level metrics belong to the supplemental parser** and key to
   `stg_property_record.record_id`, so re-matching a record never rewrites a metric; the
   property id comes through `wh_property_xref`.

## Consequences
- Schedule III gives a floor of coverage for every equity REIT on day one of a live SEC
  run; companies that aggregate Schedule III need a supplemental, website or dataset
  source, which the source map shows.
- NAV models can sum property-level inputs by owner and date, and reconcile to the
  Schedule III gross real estate total (a DQ check does this).
- Review work is visible: `property-review` lists open close calls.
- `wh_property_snapshot` (baseline, never filled) is left to the supplemental parser.
