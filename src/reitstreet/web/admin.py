"""Administration of the client web application schema (ADR 0013), for staff, from the CLI.

There is deliberately no self sign-up: organizations and users are created here, and a user
receives a single-use invitation link to set their own password on the site. Passwords are
never handled by this tool. Tokens are printed once and only their SHA-256 is stored.

Entitlements are rows (plan or organization, kind, value, validity dates). They are never
deleted: `revoke` ends a grant by setting its last day, so history stays auditable.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..core.db import Db
from ..core.errors import ConfigError

ENT_KINDS = {
    "dataset": {"companies", "metrics", "metrics_history", "filings"},
    "research": None,  # any research code, e.g. core
    "format": {"csv", "xlsx", "parquet"},
    "coverage_all": {""},
    "coverage_sector": None,  # sector name as in ref_company.sector
    "coverage_ticker": None,
    "history_years": None,  # positive integer
    "max_rows": None,  # positive integer
}
ROLES = {"member", "org_admin", "staff", "staff_admin"}
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    """256 bits, URL safe."""
    return secrets.token_urlsafe(32)


def _plan_id(db: Db, code: str) -> int:
    pid = db.scalar("SELECT plan_id FROM app_plan WHERE plan_code=:c", {"c": code})
    if pid is None:
        raise ConfigError(f"unknown plan {code!r}")
    return int(pid)


def _org(db: Db, slug: str) -> dict[str, Any]:
    r = db.one("SELECT * FROM app_organization WHERE slug=:s", {"s": slug})
    if r is None:
        raise ConfigError(f"unknown organization {slug!r}")
    return r


def validate_grant(kind: str, value: str) -> str:
    if kind not in ENT_KINDS:
        raise ConfigError(f"kind must be one of {sorted(ENT_KINDS)}")
    value = (value or "").strip()
    allowed = ENT_KINDS[kind]
    if kind in ("history_years", "max_rows"):
        if not value.isdigit() or int(value) <= 0:
            raise ConfigError(f"{kind} needs a positive whole number")
    elif kind == "coverage_ticker":
        value = value.upper()
        if not re.fullmatch(r"[A-Z0-9.\-]{1,16}", value):
            raise ConfigError("coverage_ticker needs a ticker")
    elif allowed is not None and value not in allowed:
        raise ConfigError(f"{kind} value must be one of {sorted(allowed)}")
    elif allowed is None and not value:
        raise ConfigError(f"{kind} needs a value")
    return value


# ------------------------------------------------------------------ plans and grants
def plan_create(db: Db, code: str, name: str, description: str | None) -> int:
    if not SLUG_RE.match(code.replace("_", "-")):
        raise ConfigError("plan code: lower case letters, digits, - or _")
    with db.transaction():
        return db.execute(
            "INSERT INTO app_plan (plan_code, name, description) VALUES (:c, :n, :d)",
            {"c": code, "n": name, "d": description},
        )


def grant(
    db: Db,
    *,
    kind: str,
    value: str,
    plan: str | None = None,
    org: str | None = None,
    valid_from: date | None = None,
    valid_to: date | None = None,
    by: str | None = None,
    note: str | None = None,
) -> int:
    if (plan is None) == (org is None):
        raise ConfigError("give exactly one of --plan or --org")
    value = validate_grant(kind, value)
    with db.transaction():
        return db.execute(
            """INSERT INTO app_entitlement (plan_id, org_id, kind, value, valid_from, valid_to, granted_by, note)
               VALUES (:p, :o, :k, :v, :f, :t, :b, :n)""",
            {
                "p": _plan_id(db, plan) if plan else None,
                "o": _org(db, org)["org_id"] if org else None,
                "k": kind,
                "v": value,
                "f": valid_from,
                "t": valid_to,
                "b": by,
                "n": note,
            },
        )


def revoke(db: Db, entitlement_id: int, last_day: date | None = None) -> int:
    """End a grant (never delete it). Default: it stops applying today."""
    last = last_day or (datetime.now(UTC).date() - timedelta(days=1))
    with db.transaction():
        return db.execute(
            "UPDATE app_entitlement SET valid_to=:d WHERE entitlement_id=:i AND (valid_to IS NULL OR valid_to > :d)",
            {"d": last, "i": entitlement_id},
        )


# ------------------------------------------------------------------ organizations and users
def org_create(
    db: Db,
    slug: str,
    name: str,
    *,
    plan: str | None = None,
    seats: int | None = None,
    internal: bool = False,
    contract_start: date | None = None,
    contract_end: date | None = None,
) -> int:
    if not SLUG_RE.match(slug):
        raise ConfigError("slug: 3 to 64 lower case letters, digits or -")
    with db.transaction():
        return db.execute(
            """INSERT INTO app_organization (slug, name, plan_id, seat_limit, is_internal, contract_start, contract_end)
               VALUES (:s, :n, :p, :seats, :i, :cs, :ce)""",
            {
                "s": slug,
                "n": name,
                "p": _plan_id(db, plan) if plan else None,
                "seats": seats,
                "i": int(internal),
                "cs": contract_start,
                "ce": contract_end,
            },
        )


def org_set(
    db: Db,
    slug: str,
    *,
    plan: str | None = None,
    status: str | None = None,
    seats: int | None = None,
    contract_end: date | None = None,
) -> int:
    org = _org(db, slug)
    sets: dict[str, Any] = {}
    if plan is not None:
        sets["plan_id"] = _plan_id(db, plan)
    if status is not None:
        if status not in ("active", "suspended", "closed"):
            raise ConfigError("status must be active, suspended or closed")
        sets["status"] = status
    if seats is not None:
        sets["seat_limit"] = seats
    if contract_end is not None:
        sets["contract_end"] = contract_end
    if not sets:
        return 0
    with db.transaction():
        n = db.execute(
            f"UPDATE app_organization SET {', '.join(f'{k}=:{k}' for k in sets)} WHERE org_id=:id",
            {**sets, "id": org["org_id"]},
        )
        if status in ("suspended", "closed"):
            _revoke_sessions(db, "u.org_id=:o", {"o": org["org_id"]})
        return n


def _revoke_sessions(db: Db, where: str, params: dict[str, Any]) -> int:
    return db.execute(
        f"""UPDATE app_session s JOIN app_user u ON u.user_id = s.user_id
            SET s.revoked_at = UTC_TIMESTAMP(6) WHERE s.revoked_at IS NULL AND {where}""",
        params,
    )


@dataclass
class Link:
    email: str
    purpose: str
    url: str
    expires_at: datetime


def _issue_token(db: Db, user_id: int, email: str, purpose: str, days: float, base_url: str, by: str | None) -> Link:
    token = new_token()
    expires = _now() + timedelta(days=days)
    # a new link replaces any earlier unused one, so only the latest link works
    db.execute(
        "UPDATE app_user_token SET used_at=UTC_TIMESTAMP(6) WHERE user_id=:u AND purpose=:p AND used_at IS NULL",
        {"u": user_id, "p": purpose},
    )
    db.execute(
        """INSERT INTO app_user_token (token_hash, user_id, purpose, created_by, expires_at)
           VALUES (:h, :u, :p, :b, :e)""",
        {"h": token_hash(token), "u": user_id, "p": purpose, "b": by, "e": expires},
    )
    path = "invite" if purpose == "invite" else "reset"
    return Link(email, purpose, f"{base_url.rstrip('/')}/{path}/{token}", expires)


def user_invite(
    db: Db,
    org: str,
    email: str,
    *,
    name: str | None = None,
    role: str = "member",
    days: float = 7,
    base_url: str = "http://localhost:3000",
    by: str | None = None,
) -> Link:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise ConfigError(f"not an email address: {email!r}")
    if role not in ROLES:
        raise ConfigError(f"role must be one of {sorted(ROLES)}")
    o = _org(db, org)
    if role.startswith("staff") and not o["is_internal"]:
        raise ConfigError("staff roles are only for the internal organization")
    with db.transaction():
        existing = db.one("SELECT user_id, org_id, status FROM app_user WHERE email=:e", {"e": email})
        if existing is not None:
            # inviting someone again only replaces their link; it takes no new seat
            if existing["org_id"] != o["org_id"] or existing["status"] != "invited":
                raise ConfigError(f"{email} already has an account; use user-reset or user-enable")
            uid = int(existing["user_id"])
        else:
            if o["seat_limit"] is not None:
                used = db.scalar(
                    "SELECT COUNT(*) FROM app_user WHERE org_id=:o AND status <> 'disabled'", {"o": o["org_id"]}
                )
                if int(used) >= int(o["seat_limit"]):
                    raise ConfigError(f"{org} has used all {o['seat_limit']} seats")
            uid = db.execute(
                "INSERT INTO app_user (org_id, email, full_name, role) VALUES (:o, :e, :n, :r)",
                {"o": o["org_id"], "e": email, "n": name, "r": role},
            )
        return _issue_token(db, uid, email, "invite", days, base_url, by)


def user_reset(
    db: Db, email: str, *, days: float = 1, base_url: str = "http://localhost:3000", by: str | None = None
) -> Link:
    email = email.strip().lower()
    with db.transaction():
        u = db.one("SELECT user_id, status FROM app_user WHERE email=:e", {"e": email})
        if u is None:
            raise ConfigError(f"no account for {email}")
        if u["status"] == "disabled":
            raise ConfigError(f"{email} is disabled; enable it first")
        purpose = "invite" if u["status"] == "invited" else "password_reset"
        return _issue_token(db, int(u["user_id"]), email, purpose, days, base_url, by)


def user_set_status(db: Db, email: str, status: str) -> int:
    if status not in ("active", "disabled"):
        raise ConfigError("status must be active or disabled")
    email = email.strip().lower()
    with db.transaction():
        u = db.one("SELECT user_id, password_hash FROM app_user WHERE email=:e", {"e": email})
        if u is None:
            raise ConfigError(f"no account for {email}")
        if status == "active" and not u["password_hash"]:
            status = "invited"
        n = db.execute("UPDATE app_user SET status=:s WHERE user_id=:u", {"s": status, "u": u["user_id"]})
        if status == "disabled":
            _revoke_sessions(db, "u.user_id=:u", {"u": u["user_id"]})
        return n


# ------------------------------------------------------------------ research
FRONT_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
ARTICLE_KEYS = {"slug", "title", "summary", "research", "tickers", "sectors", "author", "status"}


def parse_article(text: str) -> tuple[dict[str, str], str]:
    """Markdown with a simple front matter block of `key: value` lines."""
    m = FRONT_RE.match(text)
    if not m:
        raise ConfigError("article needs a front matter block (--- key: value ---) with slug and title")
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        k, sep, v = line.partition(":")
        if not sep:
            raise ConfigError(f"front matter line without a colon: {line!r}")
        k = k.strip().lower()
        if k not in ARTICLE_KEYS:
            raise ConfigError(f"unknown front matter key {k!r}; allowed: {sorted(ARTICLE_KEYS)}")
        meta[k] = v.strip().strip('"')
    for req in ("slug", "title"):
        if not meta.get(req):
            raise ConfigError(f"front matter needs {req}")
    if not SLUG_RE.match(meta["slug"]):
        raise ConfigError("slug: lower case letters, digits or -")
    status = meta.get("status", "draft")
    if status not in ("draft", "published", "withdrawn"):
        raise ConfigError("status must be draft, published or withdrawn")
    meta["status"] = status
    return meta, text[m.end() :].strip() + "\n"


def article_save(db: Db, path: Path, *, by: str | None = None, status: str | None = None) -> dict[str, Any]:
    meta, body = parse_article(path.read_text(encoding="utf-8"))
    if status:
        meta["status"] = status
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    with db.transaction():
        cur = db.one("SELECT * FROM app_research_article WHERE slug=:s FOR UPDATE", {"s": meta["slug"]})
        fields = {
            "slug": meta["slug"],
            "title": meta["title"],
            "summary": meta.get("summary") or None,
            "body": body,
            "rc": meta.get("research") or "core",
            "tickers": ",".join(t.strip().upper() for t in meta.get("tickers", "").split(",") if t.strip()) or None,
            "sectors": ",".join(t.strip() for t in meta.get("sectors", "").split(",") if t.strip()) or None,
            "author": meta.get("author") or None,
            "status": meta["status"],
        }
        if cur is None:
            aid = db.execute(
                """INSERT INTO app_research_article
                   (slug, title, summary, body_md, research_code, tickers, sectors, author, status, version,
                    published_at)
                   VALUES (:slug, :title, :summary, :body, :rc, :tickers, :sectors, :author, :status, 1,
                           IF(:status='published', UTC_TIMESTAMP(6), NULL))""",
                fields,
            )
            version = 1
        else:
            unchanged = (
                cur["body_md"] == body
                and cur["title"] == fields["title"]
                and cur["status"] == fields["status"]
                and cur["summary"] == fields["summary"]
                and cur["research_code"] == fields["rc"]
                and cur["tickers"] == fields["tickers"]
                and cur["sectors"] == fields["sectors"]
                and cur["author"] == fields["author"]
            )
            if unchanged:
                return {"slug": meta["slug"], "version": cur["version"], "changed": False}
            aid = int(cur["article_id"])
            version = int(cur["version"]) + 1
            db.execute(
                """UPDATE app_research_article SET title=:title, summary=:summary, body_md=:body, research_code=:rc,
                       tickers=:tickers, sectors=:sectors, author=:author, status=:status, version=:v,
                       published_at=COALESCE(published_at, IF(:status='published', UTC_TIMESTAMP(6), NULL))
                   WHERE article_id=:id""",
                {**fields, "v": version, "id": aid},
            )
        db.execute(
            """INSERT INTO app_research_article_version
               (article_id, version, title, body_md, body_sha256, status, changed_by)
               VALUES (:a, :v, :t, :b, :h, :s, :by)""",
            {"a": aid, "v": version, "t": fields["title"], "b": body, "h": sha, "s": fields["status"], "by": by},
        )
    return {"slug": meta["slug"], "version": version, "changed": True, "status": fields["status"]}


# ------------------------------------------------------------------ reporting
def summary(db: Db) -> dict[str, Any]:
    orgs = db.query(
        """SELECT o.slug, o.name, o.status, p.plan_code AS plan, o.seat_limit, o.contract_end,
                  (SELECT COUNT(*) FROM app_user u WHERE u.org_id=o.org_id AND u.status='active') AS active_users,
                  (SELECT COUNT(*) FROM app_user u WHERE u.org_id=o.org_id AND u.status='invited') AS invited_users
           FROM app_organization o LEFT JOIN app_plan p ON p.plan_id=o.plan_id ORDER BY o.slug"""
    )
    grants = db.query(
        """SELECT e.entitlement_id AS id, p.plan_code AS plan, o.slug AS org, e.kind, e.value, e.valid_from, e.valid_to
           FROM app_entitlement e LEFT JOIN app_plan p ON p.plan_id=e.plan_id
           LEFT JOIN app_organization o ON o.org_id=e.org_id ORDER BY e.entitlement_id"""
    )
    return json.loads(json.dumps({"organizations": orgs, "entitlements": grants}, default=str))
