# ADR 0001: Layered tables in one MySQL schema

Status: accepted, 2026-11-26

## Context
REIT data arrives in unstable formats (JSON APIs, HTML index pages, PDFs, email) and must
reach clients as a stable product that can be audited years later.

## Decision
One schema with six layers distinguished by table prefix: `raw_` (bytes as received),
`ref_` (reference data), `stg_` (parsed source records), `wh_` (curated warehouse),
`dl_` (client contract views) and `ops_` (operations). Data only flows downward; each
layer can be rebuilt from the one above it. Every table and column carries a COMMENT,
enforced by a test, and the data dictionary is generated from the live schema.

## Consequences
- One connection, one backup, simple joins for lineage; prefixes keep the layers visible.
- Client-facing changes are contained in `dl_*`. A breaking change ships as a new view
  (`dl_metrics_v2`) beside the old one.
- If one layer outgrows the server, it can move to its own schema without renaming tables.
