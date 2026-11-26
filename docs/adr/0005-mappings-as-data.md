# ADR 0005: Metric mappings are data, not code

Status: accepted, 2026-11-26

## Context
Coverage of a REIT dataset is mostly mapping work: which XBRL tag, or which supplemental
row label, means "same-store NOI" for each company over time. That work never ends and
is done by analysts as much as engineers.

## Decision
The metric dictionary (`ref_metric`) and mapping rules (`ref_metric_map`) are tables,
seeded from CSV files in the repo. Rules have priority, multiplier, label and exclude
patterns, optional company scope and validity dates. Seed rows are upserted by a stable
`map_key`; manual rows (`origin='manual'`) are never touched by the seeder.

## Consequences
- New coverage is a CSV row and a rebuild, not a release.
- Every warehouse value records the source fact, document, page and table it came from.
  Recording the id of the rule that matched is a planned addition.
- Rules are reviewed like code when they live in the CSV; company overrides made directly
  in the database are auditable through `origin`, `notes` and `updated_at`.
