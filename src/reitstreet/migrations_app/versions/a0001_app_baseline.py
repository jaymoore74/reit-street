"""Client web application baseline: organizations, plans, entitlements, users, sessions,
research, newsletter, downloads and audit.

Lives in its own schema (default `reitstreet_app`, ADR 0013). The web login `rs_web` gets
SELECT, INSERT and UPDATE here and no DELETE, so the audit and download logs are
append-only for the application. Nothing in this schema is derived from the warehouse.

Revision ID: a0001
Revises:
Create Date: 2026-11-26
"""

from __future__ import annotations

from alembic import op

revision = "a0001"
down_revision = None
branch_labels = None
depends_on = None

T = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"

ENT_KINDS = (
    "'dataset','research','format','coverage_all','coverage_sector','coverage_ticker','history_years','max_rows'"
)

TABLES: list[tuple[str, str]] = [
    (
        "app_plan",
        f"""
CREATE TABLE app_plan (
  plan_id      SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  plan_code    VARCHAR(48) NOT NULL COMMENT 'Stable code, e.g. staff, trial',
  name         VARCHAR(128) NOT NULL COMMENT 'Display name',
  description  VARCHAR(1024) NULL COMMENT 'What the plan includes, in plain words',
  is_active    TINYINT(1) NOT NULL DEFAULT 1 COMMENT '0 once retired; organizations keep their history',
  created_at   DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'Row creation (UTC)',
  updated_at   DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'Last change (UTC)',
  PRIMARY KEY (plan_id),
  UNIQUE KEY uq_plan_code (plan_code)
) {T} COMMENT='Subscription plans. What a plan grants is in app_entitlement rows with plan_id set.'""",
    ),
    (
        "app_organization",
        f"""
CREATE TABLE app_organization (
  org_id          INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  slug            VARCHAR(64) NOT NULL COMMENT 'Stable short code, e.g. acme-capital',
  name            VARCHAR(255) NOT NULL COMMENT 'Legal or display name of the client firm',
  status          ENUM('active','suspended','closed') NOT NULL DEFAULT 'active' COMMENT 'Only active organizations can sign in',
  is_internal     TINYINT(1) NOT NULL DEFAULT 0 COMMENT '1 for our own firm (staff accounts)',
  plan_id         SMALLINT UNSIGNED NULL COMMENT 'Current plan; NULL means no plan entitlements',
  seat_limit      SMALLINT UNSIGNED NULL COMMENT 'Maximum active users; NULL means no limit',
  contract_start  DATE NULL COMMENT 'Contract start date',
  contract_end    DATE NULL COMMENT 'Contract end date; access stops after this day',
  notes           VARCHAR(1024) NULL COMMENT 'Internal notes, never shown to the client',
  created_at      DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'Row creation (UTC)',
  updated_at      DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'Last change (UTC)',
  PRIMARY KEY (org_id),
  UNIQUE KEY uq_org_slug (slug),
  CONSTRAINT fk_org_plan FOREIGN KEY (plan_id) REFERENCES app_plan (plan_id)
) {T} COMMENT='Client firms (and our own firm). Entitlements attach to the plan and to the organization.'""",
    ),
    (
        "app_entitlement",
        f"""
CREATE TABLE app_entitlement (
  entitlement_id  INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  plan_id         SMALLINT UNSIGNED NULL COMMENT 'Plan the grant belongs to (exactly one of plan_id, org_id)',
  org_id          INT UNSIGNED NULL COMMENT 'Organization the grant belongs to, for add-ons and trials',
  kind            ENUM({ENT_KINDS}) NOT NULL COMMENT 'What is granted: dataset, research series, file format, coverage, history depth or row cap',
  value           VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'Dataset code, research code, format, sector, ticker, or a number for history_years and max_rows',
  valid_from      DATE NULL COMMENT 'First day the grant applies; NULL means always',
  valid_to        DATE NULL COMMENT 'Last day the grant applies; NULL means open-ended',
  granted_by      VARCHAR(128) NULL COMMENT 'Who added the grant',
  note            VARCHAR(512) NULL COMMENT 'Why the grant exists',
  created_at      DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'Row creation (UTC)',
  PRIMARY KEY (entitlement_id),
  KEY ix_ent_plan (plan_id),
  KEY ix_ent_org (org_id),
  CONSTRAINT fk_ent_plan FOREIGN KEY (plan_id) REFERENCES app_plan (plan_id),
  CONSTRAINT fk_ent_org FOREIGN KEY (org_id) REFERENCES app_organization (org_id),
  CONSTRAINT ck_ent_owner CHECK ((plan_id IS NULL) <> (org_id IS NULL))
) {T} COMMENT='What a plan or organization may see. The web app enforces these server side on every query.'""",
    ),
    (
        "app_user",
        f"""
CREATE TABLE app_user (
  user_id              INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  org_id               INT UNSIGNED NOT NULL COMMENT 'Organization the user belongs to',
  email                VARCHAR(254) NOT NULL COMMENT 'Sign-in email, stored lower case',
  full_name            VARCHAR(255) NULL COMMENT 'Display name',
  role                 ENUM('member','org_admin','staff','staff_admin') NOT NULL DEFAULT 'member' COMMENT 'member reads; org_admin also sees its users; staff roles are for our own firm',
  status               ENUM('invited','active','disabled') NOT NULL DEFAULT 'invited' COMMENT 'invited until a password is set; disabled users cannot sign in',
  password_hash        VARCHAR(255) NULL COMMENT 'Self-describing password hash (scrypt$N$r$p$salt$hash); NULL until set',
  password_changed_at  DATETIME(6) NULL COMMENT 'UTC time the password was last set',
  failed_logins        SMALLINT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'Consecutive failed sign-ins, reset on success',
  locked_until         DATETIME(6) NULL COMMENT 'Sign-in refused until this UTC time after repeated failures',
  last_login_at        DATETIME(6) NULL COMMENT 'UTC time of the last successful sign-in',
  created_at           DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'Row creation (UTC)',
  updated_at           DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'Last change (UTC)',
  PRIMARY KEY (user_id),
  UNIQUE KEY uq_user_email (email),
  KEY ix_user_org (org_id),
  CONSTRAINT fk_user_org FOREIGN KEY (org_id) REFERENCES app_organization (org_id)
) {T} COMMENT='People who can sign in to the client site. Accounts are created by invitation only.'""",
    ),
    (
        "app_user_token",
        f"""
CREATE TABLE app_user_token (
  token_hash   CHAR(64) NOT NULL COMMENT 'SHA-256 of the single-use token; the token itself is never stored',
  user_id      INT UNSIGNED NOT NULL COMMENT 'User the token is for',
  purpose      ENUM('invite','password_reset') NOT NULL COMMENT 'Set the first password, or replace a forgotten one',
  created_by   VARCHAR(128) NULL COMMENT 'Who issued the token',
  created_at   DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'Issue time (UTC)',
  expires_at   DATETIME(6) NOT NULL COMMENT 'Token is refused after this UTC time',
  used_at      DATETIME(6) NULL COMMENT 'UTC time the token was used; a used token is refused',
  PRIMARY KEY (token_hash),
  KEY ix_token_user (user_id),
  CONSTRAINT fk_token_user FOREIGN KEY (user_id) REFERENCES app_user (user_id)
) {T} COMMENT='Single-use invitation and password reset tokens.'""",
    ),
    (
        "app_session",
        f"""
CREATE TABLE app_session (
  session_hash  CHAR(64) NOT NULL COMMENT 'SHA-256 of the session cookie value; the cookie value is never stored',
  user_id       INT UNSIGNED NOT NULL COMMENT 'Signed-in user',
  created_at    DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'Sign-in time (UTC)',
  last_seen_at  DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'Last request (UTC), updated at most once a minute',
  expires_at    DATETIME(6) NOT NULL COMMENT 'Absolute expiry (UTC)',
  revoked_at    DATETIME(6) NULL COMMENT 'Sign-out or forced revocation time (UTC)',
  ip            VARCHAR(45) NULL COMMENT 'Client IP at sign-in',
  user_agent    VARCHAR(512) NULL COMMENT 'Browser at sign-in',
  PRIMARY KEY (session_hash),
  KEY ix_session_user (user_id),
  CONSTRAINT fk_session_user FOREIGN KEY (user_id) REFERENCES app_user (user_id)
) {T} COMMENT='Server-side sessions. Revoking a row signs the browser out on its next request.'""",
    ),
    (
        "app_login_attempt",
        f"""
CREATE TABLE app_login_attempt (
  attempt_id    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  attempted_at  DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'Attempt time (UTC)',
  ip            VARCHAR(45) NOT NULL COMMENT 'Client IP',
  email         VARCHAR(254) NOT NULL COMMENT 'Email typed (lower case), whether or not an account exists',
  succeeded     TINYINT(1) NOT NULL COMMENT '1 if the sign-in succeeded',
  PRIMARY KEY (attempt_id),
  KEY ix_attempt_ip (ip, attempted_at)
) {T} COMMENT='Every sign-in attempt, for per-IP throttling and investigation.'""",
    ),
    (
        "app_research_article",
        f"""
CREATE TABLE app_research_article (
  article_id      INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  slug            VARCHAR(128) NOT NULL COMMENT 'URL path segment, stable once published',
  title           VARCHAR(255) NOT NULL COMMENT 'Headline',
  summary         VARCHAR(1024) NULL COMMENT 'One-paragraph abstract shown in lists',
  body_md         MEDIUMTEXT NOT NULL COMMENT 'Current body in Markdown (raw HTML is not rendered)',
  research_code   VARCHAR(48) NOT NULL DEFAULT 'core' COMMENT 'Entitlement needed to read the body (kind research)',
  tickers         VARCHAR(512) NULL COMMENT 'Comma-separated tickers the piece covers',
  sectors         VARCHAR(512) NULL COMMENT 'Comma-separated property sectors the piece covers',
  author          VARCHAR(255) NULL COMMENT 'Byline',
  status          ENUM('draft','published','withdrawn') NOT NULL DEFAULT 'draft' COMMENT 'Only published pieces are visible to clients',
  version         SMALLINT UNSIGNED NOT NULL DEFAULT 1 COMMENT 'Current version number; history in app_research_article_version',
  published_at    DATETIME(6) NULL COMMENT 'First publication time (UTC)',
  created_at      DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'Row creation (UTC)',
  updated_at      DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'Last change (UTC)',
  PRIMARY KEY (article_id),
  UNIQUE KEY uq_article_slug (slug),
  KEY ix_article_status (status, published_at)
) {T} COMMENT='Research notes shown on the client site, current version.'""",
    ),
    (
        "app_research_article_version",
        f"""
CREATE TABLE app_research_article_version (
  article_id   INT UNSIGNED NOT NULL COMMENT 'Article',
  version      SMALLINT UNSIGNED NOT NULL COMMENT 'Version number, 1 upwards',
  title        VARCHAR(255) NOT NULL COMMENT 'Headline at this version',
  body_md      MEDIUMTEXT NOT NULL COMMENT 'Body at this version',
  body_sha256  CHAR(64) NOT NULL COMMENT 'SHA-256 of body_md, to prove what was published',
  status       ENUM('draft','published','withdrawn') NOT NULL COMMENT 'Status set with this version',
  changed_by   VARCHAR(128) NULL COMMENT 'Who made the change',
  changed_at   DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'Change time (UTC)',
  PRIMARY KEY (article_id, version),
  CONSTRAINT fk_artv_article FOREIGN KEY (article_id) REFERENCES app_research_article (article_id)
) {T} COMMENT='Every version of every research piece ever saved. Never updated.'""",
    ),
    (
        "app_newsletter_list",
        f"""
CREATE TABLE app_newsletter_list (
  list_code    VARCHAR(48) NOT NULL COMMENT 'Stable code, e.g. weekly',
  name         VARCHAR(128) NOT NULL COMMENT 'Display name',
  description  VARCHAR(512) NULL COMMENT 'What subscribers get',
  is_public    TINYINT(1) NOT NULL DEFAULT 1 COMMENT '1 if anyone can sign up on the public page; 0 for clients only',
  is_active    TINYINT(1) NOT NULL DEFAULT 1 COMMENT '0 once retired',
  created_at   DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'Row creation (UTC)',
  PRIMARY KEY (list_code)
) {T} COMMENT='Newsletters people can subscribe to.'""",
    ),
    (
        "app_newsletter_subscription",
        f"""
CREATE TABLE app_newsletter_subscription (
  subscription_id     INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  list_code           VARCHAR(48) NOT NULL COMMENT 'Newsletter',
  email               VARCHAR(254) NOT NULL COMMENT 'Subscriber email, lower case',
  user_id             INT UNSIGNED NULL COMMENT 'Client user, when signed up from the account page',
  status              ENUM('pending','confirmed','unsubscribed') NOT NULL DEFAULT 'pending' COMMENT 'pending until the email address is confirmed (double opt-in)',
  source              ENUM('public_form','account','admin') NOT NULL COMMENT 'Where the request came from',
  consent_text        VARCHAR(1024) NOT NULL COMMENT 'Exact consent wording the person agreed to',
  confirm_token_hash  CHAR(64) NULL COMMENT 'SHA-256 of the confirmation token sent by email',
  requested_at        DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'Latest sign-up request (UTC)',
  confirmed_at        DATETIME(6) NULL COMMENT 'Confirmation time (UTC)',
  unsubscribed_at     DATETIME(6) NULL COMMENT 'Unsubscribe time (UTC)',
  ip                  VARCHAR(45) NULL COMMENT 'Client IP of the latest request',
  updated_at          DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'Last change (UTC)',
  PRIMARY KEY (subscription_id),
  UNIQUE KEY uq_sub_list_email (list_code, email),
  KEY ix_sub_user (user_id),
  CONSTRAINT fk_sub_list FOREIGN KEY (list_code) REFERENCES app_newsletter_list (list_code),
  CONSTRAINT fk_sub_user FOREIGN KEY (user_id) REFERENCES app_user (user_id)
) {T} COMMENT='Newsletter sign-ups with the consent given. No email is sent until a mail provider is set up.'""",
    ),
    (
        "app_download",
        f"""
CREATE TABLE app_download (
  download_id   CHAR(26) NOT NULL COMMENT 'Download id (time-ordered), printed in the file name and the file itself',
  user_id       INT UNSIGNED NOT NULL COMMENT 'User who downloaded',
  org_id        INT UNSIGNED NOT NULL COMMENT 'Organization at the time',
  requested_at  DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'Request time (UTC)',
  dataset       VARCHAR(48) NOT NULL COMMENT 'Dataset code, e.g. metrics',
  format        ENUM('csv','xlsx','parquet') NOT NULL COMMENT 'File format',
  params        JSON NOT NULL COMMENT 'Filters as requested (tickers, metrics, dates, as-of)',
  status        ENUM('ok','denied','error') NOT NULL COMMENT 'Outcome',
  reason        VARCHAR(512) NULL COMMENT 'Why it was denied or failed',
  row_count     INT UNSIGNED NULL COMMENT 'Rows delivered',
  byte_count    BIGINT UNSIGNED NULL COMMENT 'File size delivered',
  sha256        CHAR(64) NULL COMMENT 'SHA-256 of the file delivered, to prove what a client received',
  ip            VARCHAR(45) NULL COMMENT 'Client IP',
  PRIMARY KEY (download_id),
  KEY ix_dl_org (org_id, requested_at),
  KEY ix_dl_user (user_id, requested_at),
  CONSTRAINT fk_dl_user FOREIGN KEY (user_id) REFERENCES app_user (user_id),
  CONSTRAINT fk_dl_org FOREIGN KEY (org_id) REFERENCES app_organization (org_id)
) {T} COMMENT='Every data download attempt, allowed or not. Append-only: the web app only inserts, and its login cannot delete.'""",
    ),
    (
        "app_audit_event",
        f"""
CREATE TABLE app_audit_event (
  event_id     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Surrogate key',
  occurred_at  DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'Event time (UTC)',
  event_type   VARCHAR(48) NOT NULL COMMENT 'e.g. login_ok, login_failed, logout, password_set, view_article, download, newsletter',
  user_id      INT UNSIGNED NULL COMMENT 'Acting user, when signed in',
  org_id       INT UNSIGNED NULL COMMENT 'Acting user organization',
  target       VARCHAR(255) NULL COMMENT 'What was acted on, e.g. article slug or download id',
  detail       JSON NULL COMMENT 'Extra facts about the event',
  ip           VARCHAR(45) NULL COMMENT 'Client IP',
  user_agent   VARCHAR(512) NULL COMMENT 'Browser',
  PRIMARY KEY (event_id),
  KEY ix_audit_time (occurred_at),
  KEY ix_audit_user (user_id, occurred_at),
  KEY ix_audit_type (event_type, occurred_at)
) {T} COMMENT='Security and usage audit log of the client site. Append-only: the web app only inserts, and its login cannot delete.'""",
    ),
]

SEEDS = [
    """INSERT INTO app_plan (plan_code, name, description) VALUES
       ('staff', 'Staff', 'Our own firm: everything, for testing and support')""",
    """INSERT INTO app_entitlement (plan_id, kind, value, granted_by, note)
       SELECT p.plan_id, x.kind, x.value, 'migration a0001', 'staff plan grants everything'
       FROM app_plan p
       JOIN (SELECT 'dataset' AS kind, 'companies' AS value UNION ALL SELECT 'dataset', 'metrics'
             UNION ALL SELECT 'dataset', 'metrics_history' UNION ALL SELECT 'dataset', 'filings'
             UNION ALL SELECT 'research', 'core' UNION ALL SELECT 'format', 'csv'
             UNION ALL SELECT 'format', 'xlsx' UNION ALL SELECT 'format', 'parquet'
             UNION ALL SELECT 'coverage_all', '') x
       WHERE p.plan_code = 'staff'""",
    """INSERT INTO app_newsletter_list (list_code, name, description, is_public) VALUES
       ('weekly', 'Weekly REIT brief', 'Placeholder: weekly summary of filings and research', 1),
       ('research_alerts', 'Research alerts', 'Placeholder: a note when new research is published', 0)""",
]


def upgrade() -> None:
    for _name, ddl in TABLES:
        op.execute(ddl)
    for sql in SEEDS:
        op.execute(sql)


def downgrade() -> None:
    for name, _ddl in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {name}")
