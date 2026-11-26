# IR website coverage

Baseline from a probe of every results page in the universe on 2026-11-26 (no JavaScript).
Per company: [ir-coverage-baseline.csv](ir-coverage-baseline.csv). Live numbers:
`reitstreet ir-coverage --rows` or `SELECT coverage, COUNT(*) FROM ops_ir_coverage GROUP BY 1`.

| Coverage | Companies |
|---|---:|
| works (static HTML) | 60 |
| needs headless | 84 |
| no supplemental published | 38 |
| blocked or error | 4 |
| not probed | 3 |
| **total** | **189** |

## By platform

| Platform | Companies | works | needs headless | no supplemental | error / not probed |
|---|---:|---:|---:|---:|---:|
| Q4 | 87 | 2 | 78 | 3 | 4 |
| Notified | 45 | 23 | 2 | 19 | 1 |
| Equisolve | 22 | 15 | 0 | 7 | 0 |
| self hosted | 9 | 6 | 0 | 2 | 1 |
| WordPress | 9 | 5 | 1 | 3 | 0 |
| other | 7 | 1 | 3 | 2 | 1 |
| Investis | 3 | 3 | 0 | 0 | 0 |
| RMR | 3 | 3 | 0 | 0 | 0 |
| Cision | 2 | 1 | 0 | 1 | 0 |
| EQS | 2 | 1 | 0 | 1 | 0 |

## Known cases
- Errors: ADC and LOAN (client error, retry from the PC), HST (redirects to another host;
  update the universe URL), TRTX (robots.txt unreadable, so the crawler stays out).
- Not probed: BSRT and CPT have no results page URL in the universe file; TPTS has no IR site
  and is profiled `sec_8k`.
- Of the 60 that work, 55 list Q3 2026; EXR and CDR show stale lists. RWT publishes its
  supplement as XLSX. MDRR hosts files on Dropbox. ONL's file name says Draft.

## Still to test from the Windows PC
1. A live `reitstreet fetch-ir` over all 189 (this sandbox cannot reach IR sites).
2. Headless for the 84 script-built sites:
   `pip install -c constraints.txt -e .[headless]`, `python -m playwright install chromium`,
   `[ir] headless = true`, then `reitstreet fetch-ir --tickers <a few Q4 names>`.
3. Q4 feed keys, only once Q4 permission is settled.
