# Property master

Design: [ADR 0010](../adr/0010-property-master.md). Coverage: [property-coverage.md](../property-coverage.md).

## Load

```
reitstreet load-properties                      # Schedule III from the latest 10-K + active website lists, then build
reitstreet load-properties --years 5            # five annual reports per REIT (history for ownership)
reitstreet load-properties --tickers PLD,EQR --sources sec_schedule_iii
reitstreet geocode-properties --limit 2000      # US Census geocoder, 2 requests a second, cached
reitstreet property-coverage --out coverage.csv --rows
```

`load-properties` needs `sec-sync` first: it uses the 10-K filings and index pages that
sync already recorded. It is resumable (`--resume`) like every other job.

## Import a file

Any CSV, XLSX, JSON or HTML table with one row per property:

```
reitstreet import-properties pld_2025q2.xlsx --ticker PLD --as-of 2025-06-30 --records-path "Portfolio"
reitstreet import-properties towers.csv --ticker-column Owner --source-kind public_dataset --as-of 2026-10-01
```

Columns are guessed from the headers and the guess is printed. Pin it with
`--field-map '{"name": "Property", "city": "City", "rentable_sf": "Square Feet"}'`, and for a
website source put the same JSON in `field_map` in `property_sources.csv`.

## Review close matches

```
reitstreet property-review list --ticker PLD
reitstreet property-review confirm --record 1234 --property 567   # the record is property 567
reitstreet property-review reject --record 1234                   # it is none of the candidates
reitstreet property-review merge --from 890 --property 567        # 890 and 567 are one building
```

Manual decisions are never undone by a rebuild. The `ops.property_review_queue` check counts
what is waiting.

## After a matcher or parser change

```
reitstreet load-properties --force          # re-stage with the new parser; old rows are retired
reitstreet build-properties --rematch       # re-link every automatic link; ids stay
```

## Checks

| Check | Means |
|---|---|
| `recon.schedule_iii_total` | Schedule III rows do not add up to the reported total within 1% |
| `contract.property_owner` | an active property has no open ownership spell |
| `contract.property_records_placed` | staged records not yet linked: run `build-properties` |
| `anomaly.property_overowned` | open shares of a property add to more than 100% |
| `anomaly.property_count_jump` | a company's count moved more than 25% between two snapshots of one source |
| `anomaly.property_geocode_state` | the geocoded point is in another state than reported |
| `ops.property_review_queue` | close matches waiting for a person |
