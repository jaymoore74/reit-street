# REIT Street

Point-in-time REIT data from public disclosure: every SEC filing, XBRL fact and earnings
exhibit for each US REIT, plus company supplementals, in a bitemporal MySQL warehouse
with lineage from every number back to the page it came from.

## What it does today

- Maintains the REIT universe from `universe/reits.csv` (tickers, CIKs, IR pages).
- Pulls SEC submissions, filing index pages, 8-K EX-99 exhibits (earnings releases and
  filed supplementals) and XBRL companyfacts, rate limited and retried.
- Stores every byte received in a content-addressed raw store; can rebuild everything
  from it without the network (`replay`).
- Maps XBRL tags and supplemental row labels to 110 standard REIT metrics using rules
  held as data, derives discrete quarters, and keeps every restatement as a version.
- Parses supplemental PDFs into company metrics (FFO, same-store, leasing, sector figures)
  and schedules: debt maturities and instruments, lease expirations, top tenants,
  development pipeline and property-level facts. Every value carries file hash, page,
  table and a confidence score; doubtful values are quarantined, not loaded (ADR 0011).
- Knows which metrics matter for each property sector, so `company-profile --ticker NNN`
  returns everything relevant for a net lease REIT and nothing meant for hotels.
- Imports an archive of older supplementals (PDF, XLSX, CSV), de-duplicated by hash.
- Crawls each REIT's IR results page politely (robots.txt, per-host limits, conditional
  requests), records every document link, and downloads new supplementals; alerts on
  layout breaks and overdue supplementals. Coverage: [docs/ir-coverage.md](docs/ir-coverage.md).
- Runs data quality checks (contracts, accounting identities, anomalies, reconciliation of
  supplementals against XBRL) and a monitor that alerts by email or webhook.
- Exports client CSVs with a manifest, as current, as known on a date, or as recorded
  at a time.

Next: the property master and NAV models, the IR email inbox and a client API. See
[docs/architecture.md](docs/architecture.md).

## Production (AWS)

Runs on the Aurora MySQL cluster with scheduled GitHub Actions on a self-hosted runner in
the same VPC; raw files go to S3. Setup: [docs/runbooks/cloud-setup.md](docs/runbooks/cloud-setup.md).

## Quick start (Windows)

Full steps: [docs/runbooks/first-run-windows.md](docs/runbooks/first-run-windows.md).

```powershell
py -m venv .venv; .venv\Scripts\Activate.ps1
pip install -c constraints.txt -e .
copy config\config.example.ini config.ini     # then set [sec] user_agent to YOUR name and email
setx REITSTREET_DB_PASSWORD "..."
reitstreet migrate
reitstreet universe
reitstreet sec-sync --full-history
reitstreet spotcheck --tickers PLD,O,SPG
```

SEC requires a User-Agent with a real name and email. The pipeline refuses to call SEC
until `[sec] user_agent` in `config.ini` is set.

## Commands

| Command | Does |
|---|---|
| `config` | print effective settings, secrets masked |
| `migrate` | upgrade the schema (Alembic) and seed reference data |
| `seed` | reload the metric dictionary and mapping rules from CSV |
| `universe` | import the universe CSV |
| `sec-sync` | submissions, filing indexes, exhibits, XBRL; rebuild metrics |
| `build-metrics` | rebuild the warehouse from staging, no network |
| `parse-docs` | parse fetched documents with the registered parsers |
| `import-archive` | import a folder of older supplementals |
| `fetch-ir` | find and download new supplementals on IR websites |
| `ir-coverage` | IR coverage by company, overdue supplementals |
| `replay` | re-parse staging from the raw store, no network |
| `dq` | data quality checks |
| `monitor` | run health checks and alerts |
| `export` | client CSVs and manifest |
| `spotcheck` | key numbers with their source filing |
| `status` | row counts and recent runs |
| `dead-letter` | list, show, resolve or ignore failed items |
| `db-docs` | regenerate the data dictionary |
| `company-profile` | every relevant metric and schedule for one company |
| `parse-coverage` | supplemental parse coverage by company |
| `parse-eval` | parser quality on a folder of PDFs, no database needed |

`reitstreet <command> --help` for options. Exit codes: 0 ok, 1 failed or partial,
2 configuration error, 3 already running, 4 source blocked us.

## Documentation

- [Architecture](docs/architecture.md)
- [Data dictionary](docs/data-dictionary.md) (generated)
- [IR website coverage](docs/ir-coverage.md)
- [Runbooks](docs/runbooks/): cloud setup (AWS), first run, daily operations, IR fetcher, SEC 403, dead letters, replay,
  metric mappings, archive import, backup and restore, schema changes
- [Architecture decision records](docs/adr/README.md)
- [Contributing](CONTRIBUTING.md)

## Layout

```
src/reitstreet/       core, sources, parsers, reference, warehouse, quality, delivery, migrations
tests/unit/           pure functions, no database
tests/integration/    full pipeline against MySQL with offline SEC fixtures for 12 REITs
config/               config.example.ini (copy to ./config.ini, which git ignores)
scripts/windows/      scheduled task scripts (workstation)
deploy/aws/           runner setup, IAM policies, database bootstrap, S3 copy
.github/workflows/    CI and the scheduled production jobs (ops-*)
universe/reits.csv    the REIT universe
docs/                 architecture, data dictionary, runbooks, ADRs
```

Data (`data/`), `config.ini` and secrets are never committed.
