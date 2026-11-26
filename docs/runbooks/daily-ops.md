# Daily operations

## What runs

| Task | Script | When | Does |
|---|---|---|---|
| sync | `sync.bat` | weekdays every 30 min, 1 PM to 8 PM | `sec-sync`, `dq`, `monitor` |
| weekly | `weekly.bat` | Sunday 6 AM | `universe`, `sec-sync --force`, `fetch-ir`, `parse-docs`, `dq`, `export`, `monitor` |
| monitor | `monitor.bat` | hourly | stale runs, failures, overdue sync, DQ errors, old dead letters |

## Morning check (2 minutes)

```powershell
reitstreet status                 # row counts, last runs and their status
reitstreet dead-letter list       # anything that failed
```

Then look at alerts: `SELECT created_at, severity, title FROM ops_alert ORDER BY alert_id DESC LIMIT 20;`

## Reading run status

- `succeeded`: everything processed.
- `partial`: some items went to the dead-letter queue. See [dead-letters.md](dead-letters.md).
- `failed`: nothing useful happened. Read `error_summary` in `ops_job_run` and the log in `data\logs`.
- `aborted`: the process died (heartbeat went stale). Re-run with `--resume <run_id>`.

## Exit codes

0 ok, 1 failed or partial, 2 configuration error, 3 another copy is running, 4 the source
blocked us (see [sec-403.md](sec-403.md)).

## Logs

`data\logs\reitstreet_YYYYMMDD.jsonl`, one JSON object per line, with `run_id` on every
line of a job. Filter with PowerShell: `Select-String '"run_id": 42' data\logs\*.jsonl`.
