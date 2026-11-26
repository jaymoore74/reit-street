# REIT Street client website

Where clients sign in to read research, look at charts, download data (CSV, Excel,
Parquet) and manage newsletters. Next.js 16 with TypeScript. Design and reasons:
[ADR 0013](../../docs/adr/0013-client-website.md). Not deployed anywhere yet; how to host it
is in [web-hosting](../../docs/runbooks/web-hosting.md). Wording still to approve:
[web-copy-review](../../docs/web-copy-review.md).

## What a client can do

| Page | What it does |
|---|---|
| `/` | public page (placeholder copy), newsletter sign-up, sign-in link |
| `/login`, `/invite/<token>`, `/reset/<token>` | sign in; set a password from a single-use link |
| `/forgot` | email a password reset link (only when email is on; otherwise it says to ask staff) |
| `/newsletter/confirm/<token>` | confirm a public newsletter sign-up from the emailed link |
| `/app` | dashboard: coverage count, latest research, data status |
| `/app/research` | research notes their plan includes |
| `/app/companies`, `/app/companies/<ticker>` | companies in their coverage, metric charts with a table view, recent filings |
| `/app/data` | downloads with filters, their subscription terms, metric definitions |
| `/app/account` | newsletters, password, sign out other sessions |

Everything a client sees is limited by their organization's grants, checked on the server
for every page and file. Every download is logged with who, what, rows and a file hash.

## Run it on Windows (development, synthetic data)

Needs the repo set up per [first-run-windows](../../docs/runbooks/first-run-windows.md)
(Python, MySQL 8, `.venv`) and **Node.js 22 LTS** from nodejs.org.

1. In the MySQL command line client, as root, create a development login. The seed script
   only works on database names ending in `_dev`:

   ```sql
   CREATE USER 'webdev'@'localhost' IDENTIFIED BY 'pick-a-long-password';
   GRANT ALL ON reitstreet_dev.* TO 'webdev'@'localhost';
   GRANT ALL ON reitstreet_app_dev.* TO 'webdev'@'localhost';
   ```

2. Build the development databases from the synthetic test fixtures (12 made-up REIT
   histories, a demo client, a staff user, a sample research note). From the repo root:

   ```powershell
   .venv\Scripts\Activate.ps1
   $env:REITSTREET_DB_URL = "mysql+pymysql://webdev:pick-a-long-password@127.0.0.1:3306/reitstreet_dev"
   $env:REITSTREET_DB_APP_NAME = "reitstreet_app_dev"
   python scripts\dev\seed_web.py
   ```

   It prints two single-use invitation links: `client@demo.invalid` (demo plan: 6 of the
   12 companies, CSV and Excel) and `staff@demo.invalid` (everything).

3. Configure and start the site:

   ```powershell
   cd apps\web
   copy .env.example .env.local
   notepad .env.local
   npm ci
   npm run dev
   ```

   In `.env.local` set `RS_DB_USER=webdev`, `RS_DB_PASSWORD` to the password from step 1,
   `RS_DB_WAREHOUSE=reitstreet_dev` and `RS_DB_APP=reitstreet_app_dev`.

4. Open one of the invitation links from step 2 in a browser, choose a password, and you
   are signed in. Re-running step 2 resets everything and prints new links.

To try the emails locally, set `RS_EMAIL_PROVIDER=file` and `RS_EMAIL_DIR` to an empty
folder in `.env.local`: each email the site would send is written there as a file.

## Checks

```powershell
npm run lint
npm run typecheck
npm test                    # 38 unit tests: entitlements, SQL building, exports, email, security
npm run build
npm start                   # the production server on http://localhost:3000
npm run test:e2e -- http://localhost:3000 links.json            # 31 checks
npm run test:e2e -- http://localhost:3000 links.json C:\temp\rs-mail   # 38, with RS_EMAIL_PROVIDER=file
```

The end-to-end run needs fresh links: `python scripts\dev\seed_web.py --links-file
apps\web\links.json` from the repo root. CI runs all of this on every push.

## Layout

```
src/proxy.ts            per-request Content-Security-Policy
src/app/                pages, server actions (actions.ts), /api/download, /api/health
src/components/         forms, chart, download form
src/lib/                db pools, sessions and sign-in, entitlements, SQL building,
                        datasets, exports (csv, xlsx, parquet), research, newsletter,
                        email (SES or files) and its templates
tests/unit/             vitest
tests/e2e/smoke.mjs     31 checks against a running server (38 with a mailbox), no browser
Dockerfile              production image (standalone server, non-root, RDS CA bundle)
```

Staff commands (organizations, grants, users, research): [web admin runbook](../../docs/runbooks/web-admin.md).
