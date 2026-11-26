# ADR 0006: Ingestion order: SEC first, then supplementals, IR sites, inbox

Status: accepted, 2026-11-26

## Context
Supplementals hold most of the REIT-specific data (FFO, AFFO, NOI, same-store, occupancy,
leasing, debt ladders, property lists), but they are PDFs with no standard layout.
SEC data is structured, free and complete back to 2009 for XBRL.

## Decision
1. SEC submissions, filing indexes, 8-K EX-99 exhibits and XBRL companyfacts first: they
   give the company master, the filing calendar, GAAP figures to reconcile against, and
   many supplementals filed as exhibits.
2. One document registry (`stg_document`) for every source: SEC exhibit, IR website,
   email, archive import. De-duplicated by hash.
3. A parser registry: parsers are classes chosen by document type and company; every
   parse is kept (`stg_doc_parse`) so parser versions can be compared. A manual CSV parser
   exists now; the PDF table parser is the next module.
4. IR website fetching of direct supplemental links, then the IR email inbox.

## Consequences
- Supplemental values are reconciled against XBRL by a DQ check from day one.
- The PDF parser can be built and replaced without touching ingestion.
