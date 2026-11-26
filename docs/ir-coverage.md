# IR website coverage

Baseline from a probe of every results page in the universe on 2026-11-26: first without
JavaScript, then the 91 pages that failed or needed scripts were opened in a real browser.
Per company: [ir-coverage-baseline.csv](ir-coverage-baseline.csv). Live numbers:
`reitstreet ir-coverage --rows` or `SELECT coverage, COUNT(*) FROM ops_ir_coverage GROUP BY 1`.

| Coverage | Companies |
|---|---:|
| works (static HTML) | 60 |
| works (headless browser) | 67 |
| no supplemental published | 53 |
| needs a check by hand | 5 |
| blocked or error | 2 |
| not probed | 2 |
| **total** | **189** |

127 of 189 (67%) have a fetchable supplemental. 127 of the 136 that publish one (93%).

## By platform

| Platform | Companies | works | works headless | no supplemental | check by hand | error / not probed |
|---|---:|---:|---:|---:|---:|---:|
| Q4 | 87 | 2 | 63 | 15 | 4 | 3 |
| Notified | 45 | 23 | 2 | 19 | 1 | 0 |
| Equisolve | 22 | 15 | 0 | 7 | 0 | 0 |
| self hosted | 9 | 6 | 0 | 3 | 0 | 0 |
| WordPress | 9 | 5 | 1 | 3 | 0 | 0 |
| other | 7 | 1 | 1 | 4 | 0 | 1 |
| Investis | 3 | 3 | 0 | 0 | 0 | 0 |
| RMR | 3 | 3 | 0 | 0 | 0 | 0 |
| Cision | 2 | 1 | 0 | 1 | 0 | 0 |
| EQS | 2 | 1 | 0 | 1 | 0 | 0 |

## Known cases
- Check by hand: BDN (Drupal menu not reached), BFS (archive stops at 2019), BHM (10-Q page
  only), GNL (its release says supplementals sit under a Presentations tab that did not
  load), SACH (press release file names say Supp, labeled Press Release).
- Errors: ADC shows a Cloudflare bot check (not worked around); TRTX returned an upstream
  connect error. Retry both from the runner.
- Not probed: BSRT is BSR REIT (TSX HOM.U) with no results page URL yet; TPTS has no IR
  site and is profiled `sec_8k`.
- Q4 sites often label the supplemental link "Presentation" or "Earnings Presentation"; the
  classifier lets a file name that says supplemental win over that text (CMTG, FCPT, RITM).
- Supplementals on another page than quarterly results: BNL, CUZ, FPI, INN, JBGS, KRG, LADR,
  NLOP, SUI (in an iframe, so the runner may need the iframe's own URL), UE. The universe file
  now points at those pages.
- Stale or unusual latest periods: AIV, SQFT and XHR at Q4 2025 (XHR posts an annual
  supplement only), OPI Q1 2026, EXR and MDRR Q4 2024, SRG Q3 2023, SEVN Q4 2022, CDR Q2 2022.
  RWT and PMT publish XLSX. MDRR hosts files on Dropbox. ONL's file name says Draft.

## Still to test on the runner
1. A live `reitstreet fetch-ir --headless` over all 189 (this sandbox cannot reach IR sites
   or download Chromium; the pages above were verified in a cloud browser).
2. The SEC 8-K EX-99 fallback (this sandbox cannot reach sec.gov).
3. Q4 feed keys, only once Q4 permission is settled.
