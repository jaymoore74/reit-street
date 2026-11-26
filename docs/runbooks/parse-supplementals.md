# Parse supplementals and review what was held back

How PDFs become numbers: ADR 0011. This runbook is the day-to-day.

## Measure before importing a batch

```powershell
reitstreet parse-eval D:\supplementals --out D:\supplementals\parse_eval.csv
```

No database needed. One line per file: tables found, values read, records read, and how
many of the metrics that matter for the company's sector (`core` in
`reference/data/metric_sectors.csv`) were found. The `.summary.json` next to the CSV has
the totals. Ticker and period come from the file and folder names, as `import-archive`
reads them, and the sector from the universe CSV.

## Load

```powershell
reitstreet import-archive D:\supplementals
reitstreet parse-docs
reitstreet parse-coverage --rows
reitstreet dq
```

`parse-coverage` prints per company: supplementals stored, parsed, needing review, the share
of values quarantined, core metrics with a value, and the latest date of each schedule.

## Review the quarantine

```sql
SELECT ticker, period_end_date, page_no, table_title, label, detail, raw_text, confidence, quarantine_reason
FROM ops_parse_quarantine WHERE ticker = 'PLD' ORDER BY doc_id, page_no;
```

Common reasons and the fix:

| Reason | Meaning | Fix |
|---|---|---|
| `scale unknown` | money with no "in thousands" note on the table, page or anywhere in the document | override `scale` with `{"money": 1000}` for that table title |
| `ambiguous column` | two header cells ran together, or a value sits between two columns | override `skip` if the table is not needed; otherwise report the layout |
| `no period` | the column header has no readable period | usually a guidance or pro forma table; `skip` it |
| `confidence ... below 0.8` | several small doubts added up | check the page; if the numbers are right, override `min_confidence` for that company |

## Add an override

Edit `src/reitstreet/reference/data/parse_overrides.csv` in a branch:

```
override_key,ticker,action,title_regex,page_from,page_to,params,valid_from,valid_to,notes
pld-ffo-scale,PLD,scale,funds from operations,,,"{""money"": 1000}",2019-01-01,,FFO page omits its scale note
```

Actions: `skip`, `force_kind` (`{"kind": "debt_maturity", "orientation": "records"}`),
`scale` (`{"money": 1000, "sf": 1000}`), `min_confidence` (`{"value": 0.7}`). `valid_from`
and `valid_to` bound the reporting periods it applies to, so a layout change in 2023
does not rewrite 2019. Then:

```powershell
reitstreet seed
reitstreet parse-docs --tickers PLD --reparse
```

Old parses stay in `stg_doc_parse`; the new one records its config hash.

## Everything on one company

```powershell
reitstreet company-profile --ticker NNN
reitstreet company-profile --ticker NNN --json > nnn.json
reitstreet export --tickers NNN --views company_profile,debt_maturities,top_tenants,lease_expirations,property_metrics
```
