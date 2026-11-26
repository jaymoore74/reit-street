# Property coverage

Where each REIT's property list can come from, researched 2026-11-26 for the 150 equity
REITs in the universe (mortgage REITs own loans, not buildings). The map is data:
`src/reitstreet/reference/data/property_sources.csv`, loaded into `ref_property_source` by
`migrate`, `seed` and `universe`. Live numbers come from `reitstreet property-coverage`.

## Summary

| | REITs |
|---|---|
| A full property-level list exists in at least one source | 100 |
| Only part of the portfolio, or market-level only | 25 |
| No public property-level list found | 25 |

Every equity REIT also gets a 10-K Schedule III load. How much detail it has is known for
39 of them so far (31 one row per property, 8 by market); the rest settle on the first live
SEC run.

| Source | Rows in the map | Lists properties | Loader today |
|---|---|---|---|
| 10-K Schedule III (XBRL) | 150 | detail known for 39 | yes, `load-properties` |
| Company website | 111 | 78 full, 23 partial | HTML tables and XLSX (17 sites); 45 card layouts, 14 map-only and 8 PDFs wait |
| Supplemental PDF | 32 | 21 full, 11 partial | the supplemental parser stages them through `stage_records` |
| Public dataset (FCC towers, CMS nursing and others) | 106 | to verify | `import-properties` for files; matching to the REIT is by owner name |

Website sources start as `candidate`. A person checks the page and its field map, then sets
`status=active` in the CSV; only active sources are fetched.

## Partial lists (25)

AHR, AIV, AMH, AMT, BHM, BNL, CCI, CURB, ELS, FPI, ILPT, INVH, IRM, O, OUT, PDM, PSTL, PW,
RYN, SAFE, SQFT, UNIT, WELL, WPC, WY.

## No public property-level list (25)

ADC, BSRT, CSR, CTRE, EPRT, FRMI, GTY, INN, JAN, LAMR, MDRR, MKZR, MRP, NNN, NTST, NXDT, NYC,
OLP, PINE, PK, SBAC, SBRA, SELF, SRG, SVC.

These are mostly net lease, towers, billboards and self storage. Schedule III still gives
totals and, for some, rows by state or market. NNN, for example, shows about 3,817 properties
on its website by state only, and its supplemental aggregates by tenant, state and MSA.
Next sources to try for them: state property records, store locators of the main tenants,
and the older supplementals Jason holds.

## Research quality

The website, supplemental and dataset rows come from a desk review of each company's site
and filings. Schedule III detail for 111 REITs is marked `unknown` until the first live
load. Treat `approx_property_count` as the company's own claim, used only to measure
coverage.
