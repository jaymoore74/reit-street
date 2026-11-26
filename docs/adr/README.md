# Architecture decision records

One file per decision. To change a decision, add a new ADR that supersedes the old one.

- [0001](0001-layered-single-schema.md): Layered tables in one MySQL schema
- [0002](0002-alembic-raw-sql.md): Alembic migrations with raw SQL, no ORM
- [0003](0003-content-addressed-raw-store.md): Content-addressed, immutable raw store
- [0004](0004-bitemporal-metrics.md): Bitemporal metric values
- [0005](0005-mappings-as-data.md): Metric mappings are data, not code
- [0006](0006-ingestion-roadmap.md): Ingestion order: SEC first, then supplementals, IR sites, inbox
- [0007](0007-mysql.md): MySQL 8 as the warehouse
- [0008](0008-aws-aurora-self-hosted-runner.md): Production on AWS Aurora, jobs on a self-hosted GitHub Actions runner
- [0009](0009-ir-fetcher.md): IR website fetcher: strategy chain, profiles as data, polite crawling
- [0010](0010-property-master.md): Property master: source records, stable property ids, matching, ownership history
- [0011](0011-supplemental-parser.md): Supplemental PDF parser, record tables with lineage and quarantine, sector metric catalog
- [0012](0012-nav-affo-models.md): NAV and AFFO models generated from cited inputs, one formula language for Python and Excel
