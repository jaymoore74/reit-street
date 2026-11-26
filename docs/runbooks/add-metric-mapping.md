# Add or change a metric mapping

Mappings are rows in `ref_metric_map`, seeded from
`src/reitstreet/reference/data/metric_map.csv`.

## A rule for everyone (code change, reviewed)

1. Add a row to `metric_map.csv`. `map_key` must be unique and stable:
   `xbrl:<METRIC>:<taxonomy>:<tag>:<unit>` or `supp:<METRIC>:<name>`.
2. Lower `priority` wins when several rules match the same period.
3. For supplemental rules, `label_regex` is a case-insensitive MySQL REGEXP on the row
   label; use `exclude_regex` to drop look-alikes (for example "per share").
4. New metric? Add it to `metrics.csv` first.
5. Add or extend a test, open a PR. After merge: `reitstreet seed` then `build-metrics`.

## A company-specific override (data change, no deploy)

```sql
INSERT INTO ref_metric_map (map_key, metric_id, source_type, label_regex, priority, multiplier,
                            company_id, origin, notes)
SELECT 'manual:pld-ffo', metric_id, 'supplemental', '^ffo attributable to common', 5, 1000,
       (SELECT company_id FROM ref_company WHERE ticker='PLD'), 'manual', 'PLD reports in thousands'
FROM ref_metric WHERE metric_code='FFO_NAREIT';
```

`origin='manual'` rows are never touched by `seed`. Then `reitstreet build-metrics --tickers PLD`
and `reitstreet dq --only recon`.

To retire a rule, set `is_active=0` (keep the row for audit) and rebuild.
