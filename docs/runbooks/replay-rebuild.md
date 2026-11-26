# Replay and rebuild

Everything below the raw layer can be rebuilt without the network.

| Need | Command |
|---|---|
| Mapping rule changed | `reitstreet seed` then `reitstreet build-metrics` |
| SEC parser fixed | `reitstreet replay` (re-parses stored SEC payloads, then rebuilds metrics) |
| Filing index parser fixed | `reitstreet replay --reindex` |
| Reproduce what we had at a time | `reitstreet replay --as-of 2026-06-30T00:00` |
| Document parser improved | `reitstreet parse-docs --reparse` |

## What happens to client data

`build-metrics` diffs on `row_hash`. Unchanged values are not touched. Changed values get
`retired_at` set and a new row inserted, so `export --as-recorded <before>` still returns
exactly what clients received before the change. Note the hash includes lineage, so a
replay that re-creates staging rows with new ids retires and re-inserts rows with
identical values. That is expected and shows up as churn in the job counts.

## Checks after a rebuild

```powershell
reitstreet dq
reitstreet spotcheck --tickers PLD,O,SPG
```
