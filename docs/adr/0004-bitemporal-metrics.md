# ADR 0004: Bitemporal metric values

Status: accepted, 2026-11-26

## Context
REITs restate. Clients back-testing a strategy need the value as it was known on a date,
and auditors need to know what we delivered on a date, even after we fix a mapping.

## Decision
`wh_metric_value` carries three time axes: valid time (period), knowledge time
(`known_from`, `known_to`, `version`, from filing dates) and system time
(`recorded_at`, `retired_at`). Rebuilds diff on a hash of value plus lineage and retire
changed rows instead of updating or deleting them. A generated `is_live` column keeps the
uniqueness rule on live rows only.

## Consequences
- Exports support `--as-of-date` (knowledge time) and `--as-recorded` (system time).
- No silent changes to delivered numbers; every change is a retired row plus a new row.
- The table grows with every correction; queries for current data filter on `is_live`
  and `known_to IS NULL` (the `dl_metrics` view does this).
