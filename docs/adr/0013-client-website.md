# ADR 0013: Client website

Status: accepted, 2026-11-26 (hosting: proposed, nothing deployed)

## Context
Clients need one place to sign in, read research, look at charts, download data and
manage newsletters. What they may see is a commercial term: which datasets, which file
formats, which companies, how much history. It has to be enforced on the server for every
page and every file, and every download has to be traceable to a person.

The warehouse already serves clients through the `dl_*` views (as-of semantics, lineage,
one public interface). The site should read those views and nothing else of the warehouse.

## Decision

### Stack
- **Next.js 16 (App Router) with TypeScript, React 19, in `apps/web`.** Server components
  render every page on the server, so entitlement checks and SQL never reach the browser.
  Forms are server actions with plain HTML fallbacks: the site works without JavaScript.
- **mysql2** with two connection pools (warehouse, app), TLS with a pinned CA in
  production, UTC on every connection, decimals as strings (no float rounding).
- **Recharts** for charts, **exceljs** and **hyparquet-writer** for XLSX and Parquet,
  **zod** for every input. Exact versions pinned in `package.json` and the lockfile.
- One repo: the site, the pipeline and the schema change together, in one CI.

### Data and logins
- **A second schema, `reitstreet_app`** (config `[db] app_name`), with its own Alembic
  history (`migrations_app/`, revision `a0001`, every table prefixed `app_` and every
  column commented). It holds organizations, plans, entitlements, users, sessions, tokens,
  research, newsletter lists and subscriptions, the download log and the audit log.
  Client accounts and password hashes stay out of the warehouse, so the analysts' read
  login can never see them, and the schema can get its own backup and retention.
- **A fourth login, `rs_web`**: `SELECT, SHOW VIEW` on the warehouse and `SELECT, INSERT,
  UPDATE` on the app schema. It cannot change schemas or delete rows. `rs_loader` gets
  the same app rights for the admin commands; `rs_reader` gets none; `rs_migrator` owns
  both. Schema-wide grants, because MySQL cannot grant on tables that do not exist yet and
  the bootstrap runs before the migrations.
- **Accepted risk:** the download and audit logs are append-only in code and the web
  login cannot delete, but it could `UPDATE` them if the app were compromised. Copying
  the logs to S3 nightly (planned with hosting) closes this.
- New view `dl_metric_definitions` (warehouse revision `0006`) so the site can show
  metric definitions without reading `ref_*` tables.

### Sign-in
- **Our own email and password accounts, no vendor.** Accounts are created by staff with
  `reitstreet web user-invite`, which prints a single-use link (7 days); there is no
  public sign-up. Password reset links come from `user-reset` the same way. Until an email
  service is approved, staff send these links by hand.
- Passwords: scrypt (N=2^15, r=8, p=3), 12 to 256 characters, rehashed when the cost
  changes. Unknown emails take the same time as wrong passwords.
- Sessions live in the database (only a SHA-256 of the token is stored), cookie
  `__Host-` prefixed, HttpOnly, Secure, SameSite=Lax; 7 days absolute, 12 hours idle.
  Suspending an organization or disabling a user ends their sessions at once.
- Lockout after 5 failures for 15 minutes, and at most 30 failures per IP per 15 minutes.
- Server actions get Next's Origin check; the download and sign-out routes check Origin
  themselves. Content-Security-Policy with a per-request nonce, `frame-ancestors 'none'`,
  HSTS, no referrer on token pages, `noindex` everywhere.
- Not yet: two-factor sign-in and SSO. TOTP can be added in house later with no vendor;
  SSO for client firms needs a vendor or SAML work and his decision.

### Entitlements
- **Grants are rows** (`app_entitlement`) on a plan or on one organization, each with
  `valid_from` and `valid_to`; revoking sets `valid_to`, nothing is deleted. Kinds:
  `dataset`, `format`, `research`, `coverage_all`, `coverage_sector`, `coverage_ticker`,
  `history_years`, `max_rows`.
- What a user may do is the union of the plan's and the organization's grants valid today.
  An organization that is not active, or outside its contract dates, gets nothing.
- Coverage is applied in SQL (bound parameters) to every query: lists, company pages,
  charts and downloads. A company outside coverage returns 404, not 403, so its existence
  is not confirmed. History limits clip `from` dates; row caps refuse with 413.

### Downloads
- CSV, XLSX and Parquet from four datasets (`companies`, `metrics`, `metrics_history`,
  `filings`) over the `dl_*` views, with ticker, metric, period and date filters and an
  as-of date for `metrics_history`.
- Every attempt, served or refused, is written to `app_download` with the user, filters,
  row count, byte size and SHA-256 of the file. The download id is in the response header
  and, with the licence notice, inside XLSX (About sheet) and Parquet (key-value metadata)
  files. CSV stays a plain table so it loads anywhere; its SHA-256 ties it to the log.
- CSV cells that start with `= + - @` are neutralized; decimals are exact (Parquet
  DECIMAL(28,6)).

### Hosting (proposed; needs his yes before anything is created)
In his AWS account, us-east-2, next to Aurora. Prices below are us-east-1 list prices read
on 2026-11-26; Ohio is usually the same for these services, confirm before building.

| Option | What | $/month |
|---|---|---:|
| **A (recommended)** | ECS Fargate (ARM, 0.5 vCPU, 1 GB, one task) behind an Application Load Balancer with a free ACM certificate, task in the Aurora VPC with a public IP that only the ALB can reach | about 40 to 48 |
| B | One more t4g.small EC2 instance running the container behind Caddy (automatic TLS) | about 16 |

Option A breakdown: Fargate 14.42, ALB 16.43 plus about 2 to 6 for load units, three
public IPv4 addresses 10.95 (two for the ALB, one for the task), logs and secrets about 2.
Avoiding a NAT gateway (32.85 plus data) is why the task gets a public IP. App Runner is
closed to new customers, so it is not an option. Option B is cheaper but is a server to
patch; A has no servers and adds a second task for zero-downtime deploys at about 15 more.

The image is `apps/web/Dockerfile` (Next standalone output, non-root, Node 22). The
database password reaches the task from Secrets Manager, never from the repo.

### Steps that need his yes
1. A domain name (registrar and about $15 a year) and a Route 53 hosted zone ($0.50).
   reitnav.com has been suggested; it is not bought or confirmed. Nothing in the code
   names a domain: the site's address is the one setting `RS_PUBLIC_BASE_URL`, and the
   admin commands take `--base-url` for the links they print.
2. Creating the AWS resources for option A or B, and re-running ops-bootstrap-db to
   create `rs_web` (safe to re-run; it only adds the login and grants).
3. An email service for invites, password resets and newsletters (Amazon SES is about
   $0.10 per 1,000 emails and needs a production access request).
4. Two-factor or SSO vendor, if wanted.
5. Real pricing, plan names and the public page copy (all placeholders today).

## Consequences
- The site runs today only on a developer machine against a database seeded from
  synthetic fixtures (`scripts/dev/seed_web.py`, refuses names not ending in `_dev` or
  `_test`). Every page shows a sample-data banner while `RS_SAMPLE_DATA=1`.
- Staff manage everything with `reitstreet web ...` commands; an admin screen can come
  later on the same tables.
- Adding a dataset is a `dl_*` view, a spec in `src/lib/datasets.ts` and a grant value.
- CI runs lint, types, 35 unit tests, a production build and a 30-check end-to-end run of
  the built server against MySQL on every push.
