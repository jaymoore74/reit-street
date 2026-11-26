# Client website administration

Staff manage clients, subscriptions and research with `reitstreet web ...` (ADR 0013).
Every command prints JSON. Pass `--by "Your Name"`; it is recorded on grants, links and
research versions. In production these run on the AWS runner with the `rs_loader` login;
locally they use whatever `REITSTREET_DB_URL` and `REITSTREET_DB_APP_NAME` point at.

Plans, pricing and plan names are placeholders until the business plan sets them.

## Set up a client

```powershell
# 1. a plan (once): what a tier of subscription includes
reitstreet web --by "Jason" plan-create --code core --name "Core"
reitstreet web --by "Jason" grant --plan core --kind dataset --value metrics
reitstreet web --by "Jason" grant --plan core --kind format --value csv
reitstreet web --by "Jason" grant --plan core --kind coverage_all
reitstreet web --by "Jason" grant --plan core --kind research --value core

# 2. the client firm, on that plan
reitstreet web --by "Jason" org-create --slug acme-capital --name "Acme Capital" --plan core --seats 5 --contract-end 2027-12-31

# 3. a person at the firm: prints a single-use link valid 7 days
reitstreet web --by "Jason" user-invite --org acme-capital --email ann@acme.com --name "Ann Lee"
```

Links point at the site address in `REITSTREET_WEB_BASE_URL` or `[web] base_url`
(e.g. `https://research.jwalteranalytics.com`); `--base-url` overrides it for one command,
and with neither set they point at `http://localhost:3000`.

Send the printed link to the person yourself. Once email is on (`[web] email_provider =
ses`, see [web hosting](web-hosting.md#email-optional-amazon-ses)), add `--email-link` and
the command emails it instead of printing it; with email off, `--email-link` refuses before
making a link. They open it, choose a password and are signed in. Inviting the same person
again replaces the old link and takes no extra seat.

## Grants

| Kind | Value | Effect |
|---|---|---|
| `dataset` | `companies`, `metrics`, `metrics_history`, `filings` | may download it and see it on pages |
| `format` | `csv`, `xlsx`, `parquet` | file formats allowed |
| `coverage_all` | (none) | every company |
| `coverage_sector` | sector as in the universe, e.g. `Industrial` | companies in that sector |
| `coverage_ticker` | ticker, e.g. `PLD` | one company |
| `research` | research code, e.g. `core` | notes with that code |
| `history_years` | whole number | only the last N years (default: full history) |
| `max_rows` | whole number | rows per file (default 1,000,000; Excel is capped there anyway) |

- Grant on `--plan` for everyone on the plan, or `--org` for one firm (a trial, an extra
  sector). A firm gets the union of both.
- `--valid-from` and `--valid-to` (YYYY-MM-DD) date a grant; a trial is a grant with an end.
- `reitstreet web revoke --id N` ends a grant yesterday (or `--last-day`). Grants are never
  deleted, so we can always show what a client had on a date.
- `reitstreet web list` shows every firm with its active and invited user counts, and
  every grant with its id.

## People and firms

| Task | Command |
|---|---|
| forgotten password | `web user-reset --email ann@acme.com` (link valid 1 day; `--email-link` to email it). With email on, clients can also use "Forgot password" on the sign-in page (link valid 60 minutes) |
| someone leaves | `web user-disable --email ann@acme.com` (signs them out at once, frees the seat) |
| bring back | `web user-enable --email ann@acme.com`, then `user-reset` |
| firm stops paying | `web org-set --slug acme-capital --status suspended` (all its users signed out) |
| renewal | `web org-set --slug acme-capital --contract-end 2028-12-31` |
| change plan or seats | `web org-set --slug acme-capital --plan pro --seats 10` |

A firm outside its contract dates, or not `active`, sees nothing, whatever its grants say.

## Research

A note is a Markdown file with front matter; see
[apps/web/sample/research/sample-note.md](../../apps/web/sample/research/sample-note.md).

```powershell
reitstreet web --by "Jason" article notes\industrial-q3.md                      # saves as its front matter says
reitstreet web --by "Jason" article notes\industrial-q3.md --status published   # publish
reitstreet web --by "Jason" article notes\industrial-q3.md --status withdrawn   # take down
```

Clients see published notes whose `research:` code their firm is granted. Every change
is a new version with its SHA-256; saving an unchanged file does nothing.

## Logs

- Downloads: `app_download` (who, dataset, format, filters, rows, bytes, SHA-256, status
  `ok`, `denied` or `error`). The download id is in the response header and inside Excel
  and Parquet files, so a file found elsewhere can be traced.
- Sign-ins, failed sign-ins, password changes, newsletter changes and research views:
  `app_audit_event`.
- Both are append-only. Read them with the `rs_loader` or `rs_migrator` login; `rs_reader`
  cannot see the website schema.
