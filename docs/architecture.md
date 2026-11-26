# Architecture

REIT Street turns public REIT disclosure (SEC EDGAR and company supplementals) into a
point-in-time, fully traceable REIT dataset. It is built to run unattended for years
while its sources change format underneath it.

## Principles

1. **Never lose a byte.** Everything received is stored once, content-addressed, and never
   modified. Any downstream table can be rebuilt from it without the network.
2. **Every number has a receipt.** Each warehouse value points to the staging fact it came
   from, which points to the raw object, filing, document, page and cell.
3. **Nothing is overwritten.** Restatements are new versions; corrections retire old rows.
   Clients can ask "what did we know on date D" and "what did we publish at time T".
4. **Rules are data.** XBRL tag and supplemental label mappings live in tables seeded from
   CSV, so coverage grows without code changes.
5. **Fail loudly, keep going.** One bad company or document goes to the dead-letter queue;
   the run continues, is marked partial, and alerts. A source-level block (SEC 403) stops
   the run immediately.
6. **Everything is re-runnable.** Jobs are idempotent, resumable and serialized by a lock.

## Layers

One MySQL schema, tables prefixed by layer. The full column list is in
[data-dictionary.md](data-dictionary.md), generated from the live schema.

```
             sources                        layers (MySQL)                      consumers
 ┌───────────────────────────┐   ┌───────────────────────────────────────┐
 │ data.sec.gov submissions  │   │ raw_object  (sha256, gz, immutable)   │
 │ data.sec.gov companyfacts │──>│ raw_fetch   (every attempt, status)   │
 │ EDGAR filing index pages  │   └───────────────┬───────────────────────┘
 │ 8-K EX-99 exhibits        │                   │ parse (re-runnable: replay)
 │ IR websites (PDF links)   │   ┌───────────────▼───────────────────────┐
 │ archive folder import     │   │ stg_sec_filing, stg_sec_filing_doc    │
 │ IR email inbox (planned)  │   │ stg_xbrl_concept, stg_xbrl_fact       │
 └───────────────────────────┘   │ stg_document, stg_doc_parse,          │
                                 │ stg_doc_table, stg_doc_line_item      │
  ref_company, ref_ir_source ───>└───────────────┬───────────────────────┘
  ref_metric, ref_metric_map ────────────────────┤ build-metrics (rules as data)
                                 ┌───────────────▼───────────────────────┐
                                 │ wh_metric_value (bitemporal)          │   dl_* views ──> export CSV
                                 │ wh_property, wh_property_snapshot,    │   + manifest ──> client feed
                                 │ wh_debt_maturity                      │                  (API: planned)
                                 └───────────────────────────────────────┘
  ops_job_run, ops_job_item, ops_dead_letter, ops_dq_result, ops_alert  (operations)
```

| Prefix | Layer | Contract |
|---|---|---|
| `raw_` | Raw | Append-only. `raw_object` keyed by SHA-256 of the uncompressed bytes; files at `data/raw/sha256/ab/cd/<sha>.gz`. |
| `ref_` | Reference | Companies, tickers over time, IR sources, metric dictionary, mapping rules. |
| `stg_` | Staging | Parsed source records, one row per source fact. Fully re-derivable from raw. |
| `wh_` | Warehouse | Curated, bitemporal, lineage on every row. Rows are retired, never deleted. |
| `dl_` | Delivery | Views that are the client contract. Changing one is a breaking change (see ADR 0001). |
| `ops_` | Operations | Runs, checkpoints, dead letters, data quality results, alerts. |

## Time model (`wh_metric_value`)

| Axis | Columns | Question it answers |
|---|---|---|
| Valid time | `period_start`, `period_end`, `period_type` | Which quarter or year the value describes |
| Knowledge time | `known_from`, `known_to`, `version` | What the market could know on a date (filing date; restatements are v2, v3...) |
| System time | `recorded_at`, `retired_at` | What this warehouse held at a moment (mapping changes, fixes) |

`is_live` is a generated column (1 while `retired_at` is null) so the unique key holds only
for live rows. A rebuild computes the target rows, diffs on `row_hash` (value plus lineage),
retires rows that changed and inserts the new ones. An unchanged rebuild touches nothing.

Discrete quarters: 10-Ks do not report Q4 and cash flow items are year-to-date, so USD
metrics derive Q2 to Q4 by subtraction, flagged `is_derived`, with their own versions.

## Jobs

Every command that changes data runs inside `JobRun` (`core/jobs.py`):

- takes a MySQL `GET_LOCK` per job name; a second copy exits with code 3
- writes `ops_job_run` (args, code version, counts, heartbeat) and `ops_job_item` per item
- `--resume <run_id>` skips items that finished ok in that run
- an item that raises goes to `ops_dead_letter` with the traceback; the run continues
- `FatalSourceError` (SEC 401/403) stops the run and raises a critical alert

Exit codes: 0 ok, 1 failed or partial, 2 config error, 3 lock held, 4 source blocked us.

HTTP (`core/http.py`): one token-bucket rate limiter per source (SEC capped at 10/s), retries
with backoff on 429/5xx and network errors, `Retry-After` honoured, every response stored in
the raw layer before parsing.

## Quality and monitoring

`reitstreet dq` runs registered checks (`quality/checks.py`) and stores results in
`ops_dq_result`: schema contracts (lineage present, one current version, unbroken knowledge
chain, raw objects match their hash), accounting identities (assets = liabilities + equity,
quarters sum to the fiscal year), anomalies (sign flips, quarter-on-quarter jumps over 50%),
reconciliation of supplemental values against XBRL (1%), freshness and queue health.

`reitstreet monitor` marks stale runs aborted and alerts on failed or partial runs, overdue
syncs, DQ errors and dead letters older than 3 days. Alerts go to `ops_alert`, the log, and
optionally email and a webhook, de-duplicated over 6 hours.

## Module map

| Path | Responsibility |
|---|---|
| `core/` | config, db, logging (JSON lines), http, raw store, jobs, alerts, migrations runner, data dictionary |
| `sources/sec/` | submissions, filing indexes, exhibits, companyfacts; replay from raw |
| `sources/supplementals/` | archive import, IR website fetcher |
| `sources/ir_inbox/` | slot for the IR email alert inbox (planned) |
| `parsers/` | pure functions from bytes to records; document parser registry (`documents.py`) |
| `reference/` | universe import, seed of metric dictionary and mapping rules from CSV |
| `warehouse/` | period typing, versioning, metric build |
| `quality/` | DQ checks, monitor |
| `delivery/` | client exports with manifest |
| `migrations/` | Alembic revisions (raw SQL DDL, every table and column commented) |

## Planned modules (slots exist)

1. **Supplemental PDF parser**: register a parser class in `parsers/documents.py`; it emits
   tables and line items with page and cell positions into `stg_doc_*`, then property and
   debt rows into `wh_*`. Old parses stay in `stg_doc_parse` for comparison.
2. **IR inbox**: a dedicated mailbox polled into `stg_document` with `source='email'`.
3. **Client API**: read-only service over the `dl_*` views with the same as-of semantics as export.
