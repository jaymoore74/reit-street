# Data dictionary

Generated from the database schema at Alembic revision `a0001` by `reitstreet db-docs --target app`. Do not edit by hand: change the COMMENT in a migration and regenerate.

## app_* Client web application: organizations, entitlements, users, sessions, research, audit (ADR 0013)

- [`app_audit_event`](#app_audit_event): Security and usage audit log of the client site. Append-only: the web app only inserts, and its login cannot delete.
- [`app_download`](#app_download): Every data download attempt, allowed or not. Append-only: the web app only inserts, and its login cannot delete.
- [`app_entitlement`](#app_entitlement): What a plan or organization may see. The web app enforces these server side on every query.
- [`app_login_attempt`](#app_login_attempt): Every sign-in attempt, for per-IP throttling and investigation.
- [`app_newsletter_list`](#app_newsletter_list): Newsletters people can subscribe to.
- [`app_newsletter_subscription`](#app_newsletter_subscription): Newsletter sign-ups with the consent given. No email is sent until a mail provider is set up.
- [`app_organization`](#app_organization): Client firms (and our own firm). Entitlements attach to the plan and to the organization.
- [`app_plan`](#app_plan): Subscription plans. What a plan grants is in app_entitlement rows with plan_id set.
- [`app_research_article`](#app_research_article): Research notes shown on the client site, current version.
- [`app_research_article_version`](#app_research_article_version): Every version of every research piece ever saved. Never updated.
- [`app_session`](#app_session): Server-side sessions. Revoking a row signs the browser out on its next request.
- [`app_user`](#app_user): People who can sign in to the client site. Accounts are created by invitation only.
- [`app_user_token`](#app_user_token): Single-use invitation and password reset tokens.

### app_audit_event

Table. Security and usage audit log of the client site. Append-only: the web app only inserts, and its login cannot delete.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `event_id` | bigint unsigned | NO | PRI | Surrogate key |
| `occurred_at` | datetime(6) | NO | MUL | Event time (UTC) |
| `event_type` | varchar(48) | NO | MUL | e.g. login_ok, login_failed, logout, password_set, view_article, download, newsletter |
| `user_id` | int unsigned | YES | MUL | Acting user, when signed in |
| `org_id` | int unsigned | YES |  | Acting user organization |
| `target` | varchar(255) | YES |  | What was acted on, e.g. article slug or download id |
| `detail` | json | YES |  | Extra facts about the event |
| `ip` | varchar(45) | YES |  | Client IP |
| `user_agent` | varchar(512) | YES |  | Browser |

### app_download

Table. Every data download attempt, allowed or not. Append-only: the web app only inserts, and its login cannot delete.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `download_id` | char(26) | NO | PRI | Download id (time-ordered), printed in the file name and the file itself |
| `user_id` | int unsigned | NO | MUL | User who downloaded |
| `org_id` | int unsigned | NO | MUL | Organization at the time |
| `requested_at` | datetime(6) | NO |  | Request time (UTC) |
| `dataset` | varchar(48) | NO |  | Dataset code, e.g. metrics |
| `format` | enum('csv','xlsx','parquet') | NO |  | File format |
| `params` | json | NO |  | Filters as requested (tickers, metrics, dates, as-of) |
| `status` | enum('ok','denied','error') | NO |  | Outcome |
| `reason` | varchar(512) | YES |  | Why it was denied or failed |
| `row_count` | int unsigned | YES |  | Rows delivered |
| `byte_count` | bigint unsigned | YES |  | File size delivered |
| `sha256` | char(64) | YES |  | SHA-256 of the file delivered, to prove what a client received |
| `ip` | varchar(45) | YES |  | Client IP |

### app_entitlement

Table. What a plan or organization may see. The web app enforces these server side on every query.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `entitlement_id` | int unsigned | NO | PRI | Surrogate key |
| `plan_id` | smallint unsigned | YES | MUL | Plan the grant belongs to (exactly one of plan_id, org_id) |
| `org_id` | int unsigned | YES | MUL | Organization the grant belongs to, for add-ons and trials |
| `kind` | enum('dataset','research','format','coverage_all','coverage_sector','coverage_ticker','history_years','max_rows') | NO |  | What is granted: dataset, research series, file format, coverage, history depth or row cap |
| `value` | varchar(64) | NO |  | Dataset code, research code, format, sector, ticker, or a number for history_years and max_rows |
| `valid_from` | date | YES |  | First day the grant applies; NULL means always |
| `valid_to` | date | YES |  | Last day the grant applies; NULL means open-ended |
| `granted_by` | varchar(128) | YES |  | Who added the grant |
| `note` | varchar(512) | YES |  | Why the grant exists |
| `created_at` | datetime(6) | NO |  | Row creation (UTC) |

### app_login_attempt

Table. Every sign-in attempt, for per-IP throttling and investigation.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `attempt_id` | bigint unsigned | NO | PRI | Surrogate key |
| `attempted_at` | datetime(6) | NO |  | Attempt time (UTC) |
| `ip` | varchar(45) | NO | MUL | Client IP |
| `email` | varchar(254) | NO |  | Email typed (lower case), whether or not an account exists |
| `succeeded` | tinyint(1) | NO |  | 1 if the sign-in succeeded |

### app_newsletter_list

Table. Newsletters people can subscribe to.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `list_code` | varchar(48) | NO | PRI | Stable code, e.g. weekly |
| `name` | varchar(128) | NO |  | Display name |
| `description` | varchar(512) | YES |  | What subscribers get |
| `is_public` | tinyint(1) | NO |  | 1 if anyone can sign up on the public page; 0 for clients only |
| `is_active` | tinyint(1) | NO |  | 0 once retired |
| `created_at` | datetime(6) | NO |  | Row creation (UTC) |

### app_newsletter_subscription

Table. Newsletter sign-ups with the consent given. No email is sent until a mail provider is set up.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `subscription_id` | int unsigned | NO | PRI | Surrogate key |
| `list_code` | varchar(48) | NO | MUL | Newsletter |
| `email` | varchar(254) | NO |  | Subscriber email, lower case |
| `user_id` | int unsigned | YES | MUL | Client user, when signed up from the account page |
| `status` | enum('pending','confirmed','unsubscribed') | NO |  | pending until the email address is confirmed (double opt-in) |
| `source` | enum('public_form','account','admin') | NO |  | Where the request came from |
| `consent_text` | varchar(1024) | NO |  | Exact consent wording the person agreed to |
| `confirm_token_hash` | char(64) | YES |  | SHA-256 of the confirmation token sent by email |
| `requested_at` | datetime(6) | NO |  | Latest sign-up request (UTC) |
| `confirmed_at` | datetime(6) | YES |  | Confirmation time (UTC) |
| `unsubscribed_at` | datetime(6) | YES |  | Unsubscribe time (UTC) |
| `ip` | varchar(45) | YES |  | Client IP of the latest request |
| `updated_at` | datetime(6) | NO |  | Last change (UTC) |

### app_organization

Table. Client firms (and our own firm). Entitlements attach to the plan and to the organization.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `org_id` | int unsigned | NO | PRI | Surrogate key |
| `slug` | varchar(64) | NO | UNI | Stable short code, e.g. acme-capital |
| `name` | varchar(255) | NO |  | Legal or display name of the client firm |
| `status` | enum('active','suspended','closed') | NO |  | Only active organizations can sign in |
| `is_internal` | tinyint(1) | NO |  | 1 for our own firm (staff accounts) |
| `plan_id` | smallint unsigned | YES | MUL | Current plan; NULL means no plan entitlements |
| `seat_limit` | smallint unsigned | YES |  | Maximum active users; NULL means no limit |
| `contract_start` | date | YES |  | Contract start date |
| `contract_end` | date | YES |  | Contract end date; access stops after this day |
| `notes` | varchar(1024) | YES |  | Internal notes, never shown to the client |
| `created_at` | datetime(6) | NO |  | Row creation (UTC) |
| `updated_at` | datetime(6) | NO |  | Last change (UTC) |

### app_plan

Table. Subscription plans. What a plan grants is in app_entitlement rows with plan_id set.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `plan_id` | smallint unsigned | NO | PRI | Surrogate key |
| `plan_code` | varchar(48) | NO | UNI | Stable code, e.g. staff, trial |
| `name` | varchar(128) | NO |  | Display name |
| `description` | varchar(1024) | YES |  | What the plan includes, in plain words |
| `is_active` | tinyint(1) | NO |  | 0 once retired; organizations keep their history |
| `created_at` | datetime(6) | NO |  | Row creation (UTC) |
| `updated_at` | datetime(6) | NO |  | Last change (UTC) |

### app_research_article

Table. Research notes shown on the client site, current version.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `article_id` | int unsigned | NO | PRI | Surrogate key |
| `slug` | varchar(128) | NO | UNI | URL path segment, stable once published |
| `title` | varchar(255) | NO |  | Headline |
| `summary` | varchar(1024) | YES |  | One-paragraph abstract shown in lists |
| `body_md` | mediumtext | NO |  | Current body in Markdown (raw HTML is not rendered) |
| `research_code` | varchar(48) | NO |  | Entitlement needed to read the body (kind research) |
| `tickers` | varchar(512) | YES |  | Comma-separated tickers the piece covers |
| `sectors` | varchar(512) | YES |  | Comma-separated property sectors the piece covers |
| `author` | varchar(255) | YES |  | Byline |
| `status` | enum('draft','published','withdrawn') | NO | MUL | Only published pieces are visible to clients |
| `version` | smallint unsigned | NO |  | Current version number; history in app_research_article_version |
| `published_at` | datetime(6) | YES |  | First publication time (UTC) |
| `created_at` | datetime(6) | NO |  | Row creation (UTC) |
| `updated_at` | datetime(6) | NO |  | Last change (UTC) |

### app_research_article_version

Table. Every version of every research piece ever saved. Never updated.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `article_id` | int unsigned | NO | PRI | Article |
| `version` | smallint unsigned | NO | PRI | Version number, 1 upwards |
| `title` | varchar(255) | NO |  | Headline at this version |
| `body_md` | mediumtext | NO |  | Body at this version |
| `body_sha256` | char(64) | NO |  | SHA-256 of body_md, to prove what was published |
| `status` | enum('draft','published','withdrawn') | NO |  | Status set with this version |
| `changed_by` | varchar(128) | YES |  | Who made the change |
| `changed_at` | datetime(6) | NO |  | Change time (UTC) |

### app_session

Table. Server-side sessions. Revoking a row signs the browser out on its next request.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `session_hash` | char(64) | NO | PRI | SHA-256 of the session cookie value; the cookie value is never stored |
| `user_id` | int unsigned | NO | MUL | Signed-in user |
| `created_at` | datetime(6) | NO |  | Sign-in time (UTC) |
| `last_seen_at` | datetime(6) | NO |  | Last request (UTC), updated at most once a minute |
| `expires_at` | datetime(6) | NO |  | Absolute expiry (UTC) |
| `revoked_at` | datetime(6) | YES |  | Sign-out or forced revocation time (UTC) |
| `ip` | varchar(45) | YES |  | Client IP at sign-in |
| `user_agent` | varchar(512) | YES |  | Browser at sign-in |

### app_user

Table. People who can sign in to the client site. Accounts are created by invitation only.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `user_id` | int unsigned | NO | PRI | Surrogate key |
| `org_id` | int unsigned | NO | MUL | Organization the user belongs to |
| `email` | varchar(254) | NO | UNI | Sign-in email, stored lower case |
| `full_name` | varchar(255) | YES |  | Display name |
| `role` | enum('member','org_admin','staff','staff_admin') | NO |  | member reads; org_admin also sees its users; staff roles are for our own firm |
| `status` | enum('invited','active','disabled') | NO |  | invited until a password is set; disabled users cannot sign in |
| `password_hash` | varchar(255) | YES |  | Self-describing password hash (scrypt$N$r$p$salt$hash); NULL until set |
| `password_changed_at` | datetime(6) | YES |  | UTC time the password was last set |
| `failed_logins` | smallint unsigned | NO |  | Consecutive failed sign-ins, reset on success |
| `locked_until` | datetime(6) | YES |  | Sign-in refused until this UTC time after repeated failures |
| `last_login_at` | datetime(6) | YES |  | UTC time of the last successful sign-in |
| `created_at` | datetime(6) | NO |  | Row creation (UTC) |
| `updated_at` | datetime(6) | NO |  | Last change (UTC) |

### app_user_token

Table. Single-use invitation and password reset tokens.

| Column | Type | Null | Key | Description |
|---|---|---|---|---|
| `token_hash` | char(64) | NO | PRI | SHA-256 of the single-use token; the token itself is never stored |
| `user_id` | int unsigned | NO | MUL | User the token is for |
| `purpose` | enum('invite','password_reset') | NO |  | Set the first password, or replace a forgotten one |
| `created_by` | varchar(128) | YES |  | Who issued the token |
| `created_at` | datetime(6) | NO |  | Issue time (UTC) |
| `expires_at` | datetime(6) | NO |  | Token is refused after this UTC time |
| `used_at` | datetime(6) | YES |  | UTC time the token was used; a used token is refused |

