# IR website fetcher

`reitstreet fetch-ir` finds supplementals on each REIT's results page and downloads the
new ones. Scheduled by `ir_fetch.bat` at 06:00 and 15:00 on weekdays, then `dq` and
`monitor`; in production the `ops-ir-fetch` GitHub Actions workflow does the same at 14:00 and 23:00 UTC. Design: [ADR 0009](../adr/0009-ir-fetcher.md).

## Commands

```powershell
reitstreet fetch-ir                              # all companies, new files only
reitstreet fetch-ir --tickers PLD,O              # some companies
reitstreet fetch-ir --tickers PLD --backfill     # no per-company cap (first load of history)
reitstreet fetch-ir --kinds supplemental,earnings_release
reitstreet fetch-ir --no-download                # discover and record links only
reitstreet fetch-ir --force                      # ignore ETag / Last-Modified
reitstreet fetch-ir --strategy headless --headless --tickers EQIX
reitstreet ir-coverage --rows --out data\ir-coverage.csv
```

## First load
Run `--backfill` a few companies at a time. Each company keeps its own host rate limit
(0.5 requests per second by default), so a full backfill of 1,800+ files takes a few hours.

## Turning on headless (script-built sites)
```powershell
pip install -c constraints.txt -e .[headless]
python -m playwright install chromium
```
Set `[ir] headless = true` in `config.ini` (or pass `--headless`). Companies whose profile
says `strategy=headless` are rendered first; any other page is rendered only when static HTML
found it script-built. One Chromium serves the whole run, images and fonts are not loaded,
and each page waits up to 20 seconds for a document link to appear.

On the AWS runner this is on: `ops-ir-fetch` installs the `headless` extra, keeps Chromium in
`/srv/reitstreet/pw-browsers`, and runs `fetch-ir --headless`. Chromium's system libraries
come from `deploy/aws/runner-setup.sh`. A runner launched before that line existed needs it
once, as root:
```bash
python3 -m venv /tmp/pw && /tmp/pw/bin/pip install "playwright==1.65.2" && /tmp/pw/bin/python -m playwright install-deps chromium
mkdir -p /srv/reitstreet/pw-browsers && chown runner:runner /srv/reitstreet/pw-browsers
```

## Alerts and what to do

| Alert | Meaning | Do |
|---|---|---|
| `<T> IR page layout changed` | the page listed supplementals and now lists none | open the page; if it moved, fix the URL in the universe file; if it now needs JavaScript set `strategy=headless` in `ir_profiles.csv` |
| `<T> IR results_page failing` | N failed checks in a row | check `raw_fetch` for the status; 403 often means a bot wall, try headless or leave to SEC 8-K |
| `<N> supplementals overdue` | next quarter past its usual lag, or the earnings release is out and no supplemental | check the site by hand; if they stopped publishing, set `expects_supplemental=0` |

## Changing a company's profile
Edit `src/reitstreet/reference/data/ir_profiles.csv`, commit, deploy, then
`reitstreet seed`. The table records which file version it came from.

## Useful queries
```sql
SELECT coverage, COUNT(*) FROM ops_ir_coverage GROUP BY 1;
SELECT ticker, last_outcome, last_supp_links, latest_supplemental_held FROM ops_ir_coverage WHERE coverage <> 'works';
SELECT status, COUNT(*) FROM stg_ir_link GROUP BY 1;   -- 'new' = backlog waiting for download
```

## Rules the fetcher keeps
No personal details in the User-Agent. robots.txt is obeyed; unreachable robots.txt means
stay out. No logins, no forms, no email alert signups.
