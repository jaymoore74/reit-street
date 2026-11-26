"""robots.txt per RFC 9309, cached per host in ref_web_host.

Rules implemented (RFC 9309 section 2):
  * the group whose user-agent token matches ours wins; otherwise the '*' group;
    several groups for the same agent are merged
  * longest matching path wins; on a tie Allow wins; '*' and '$' wildcards
  * /robots.txt itself is always allowed
  * 2xx: parse; 4xx: no restrictions; 5xx or unreachable: assume complete disallow
    (and try again next run)
Crawl-delay is not in the RFC but widely used; when set for our agent it raises the
per-host interval of the rate limiter.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, unquote, urlparse

from ...core.db import Db
from ...core.errors import TransientError
from ...core.http import Fetcher, HostRateLimiter

log = logging.getLogger(__name__)

MAX_CRAWL_DELAY_S = 30.0
MAX_ROBOTS_BYTES = 500 * 1024  # RFC 9309: parse at least 500 KiB


@dataclass
class Rules:
    policy: str  # parsed | allow_all | disallow_all
    rules: list[tuple[bool, str]] = field(default_factory=list)  # (allow, pattern)
    crawl_delay: float | None = None

    def allowed(self, url: str) -> bool:
        if self.policy == "allow_all":
            return True
        if self.policy == "disallow_all":
            return False
        p = urlparse(url)
        path = _norm(p.path or "/") + (f"?{p.query}" if p.query else "")
        if path == "/robots.txt":
            return True
        best_len, best_allow = -1, True
        for allow, pat in self.rules:
            if not pat:
                continue  # an empty Disallow allows everything
            if _match(pat, path):
                n = len(pat)
                if n > best_len or (n == best_len and allow):
                    best_len, best_allow = n, allow
        return best_allow


def _norm(path: str) -> str:
    # Compare in a single canonical encoding (RFC 9309 2.2.2).
    return quote(unquote(path), safe="/%*$?=&;:@+,!~'()")


def _match(pattern: str, path: str) -> bool:
    anchored = pattern.endswith("$")
    body = _norm(pattern[:-1] if anchored else pattern)
    rx = "".join(".*" if ch == "*" else re.escape(ch) for ch in body)
    return re.match(rx + ("$" if anchored else ""), path) is not None


def parse_robots(text: str, agent_token: str) -> Rules:
    """Rules that apply to `agent_token` (product token, e.g. ReitStreetBot)."""
    token = agent_token.lower()
    groups: list[tuple[list[str], list[tuple[bool, str]], float | None]] = []
    agents: list[str] = []
    rules: list[tuple[bool, str]] = []
    delay: float | None = None
    in_rules = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if in_rules:
                groups.append((agents, rules, delay))
                agents, rules, delay, in_rules = [], [], None, False
            agents.append(value.lower())
        elif key in ("allow", "disallow"):
            if agents:
                rules.append((key == "allow", value))
                in_rules = True
        elif key == "crawl-delay" and agents:
            try:
                delay = float(value)
            except ValueError:
                pass
            in_rules = True
    if agents:
        groups.append((agents, rules, delay))
    mine = [g for g in groups if any(a != "*" and a and a in token for a in g[0])]
    chosen = mine or [g for g in groups if "*" in g[0]]
    merged: list[tuple[bool, str]] = [r for g in chosen for r in g[1]]
    delays = [g[2] for g in chosen if g[2] is not None]
    return Rules("parsed", merged, max(delays) if delays else None)


class RobotsCache:
    """robots.txt for every host the fetcher touches. One fetch per host per TTL, and the
    rate limiter learns each host's Crawl-delay."""

    def __init__(self, fetcher: Fetcher, db: Db | None, *, agent_token: str, ttl_hours: float = 24.0):
        self.fetcher = fetcher
        self.db = db
        self.token = agent_token
        self.ttl = timedelta(hours=ttl_hours)
        self._mem: dict[str, Rules] = {}

    def allowed(self, url: str) -> bool:
        return self.rules_for(url).allowed(url)

    def rules_for(self, url: str) -> Rules:
        p = urlparse(url)
        host = (p.hostname or "").lower()
        if host in self._mem:
            return self._mem[host]
        r = self._load(host) or self._fetch(p.scheme or "https", p.netloc, host)
        self._mem[host] = r
        if r.crawl_delay and isinstance(self.fetcher.limiter, HostRateLimiter):
            self.fetcher.limiter.set_min_interval(host, min(r.crawl_delay, MAX_CRAWL_DELAY_S))
        return r

    def _load(self, host: str) -> Rules | None:
        if self.db is None:
            return None
        row = self.db.one(
            "SELECT robots_policy, robots_sha256, crawl_delay_s, robots_fetched_at FROM ref_web_host WHERE host=:h",
            {"h": host},
        )
        if not row or row["robots_fetched_at"] is None:
            return None
        fetched = row["robots_fetched_at"].replace(tzinfo=UTC)
        if datetime.now(UTC) - fetched > self.ttl:
            return None
        if row["robots_policy"] != "parsed":
            return Rules(row["robots_policy"])
        try:
            body = self.fetcher.store.get(row["robots_sha256"])
        except Exception:
            return None
        return parse_robots(body[:MAX_ROBOTS_BYTES].decode("utf-8", errors="replace"), self.token)

    def _fetch(self, scheme: str, netloc: str, host: str) -> Rules:
        url = f"{scheme}://{netloc}/robots.txt"
        status: int | None = None
        sha: str | None = None
        try:
            r = self.fetcher.get(url, allow_404=True, return_status=tuple(range(400, 500)))
            if r is None:
                status, rules = 404, Rules("allow_all")
            elif 200 <= r.status < 300:
                status, sha = r.status, r.sha256
                rules = parse_robots(r.body[:MAX_ROBOTS_BYTES].decode("utf-8", errors="replace"), self.token)
            else:
                status, rules = r.status, Rules("allow_all")
        except TransientError as e:
            log.warning(
                "robots.txt unreachable; treating host as disallowed this run", extra={"host": host, "err": str(e)}
            )
            rules = Rules("disallow_all")
        if self.db is not None:
            self.db.execute(
                """INSERT INTO ref_web_host (host, robots_status, robots_policy, robots_sha256, crawl_delay_s,
                     robots_fetched_at) VALUES (:h, :st, :p, :sha, :cd, UTC_TIMESTAMP(6)) AS new
                   ON DUPLICATE KEY UPDATE robots_status=new.robots_status, robots_policy=new.robots_policy,
                     robots_sha256=new.robots_sha256, crawl_delay_s=new.crawl_delay_s,
                     robots_fetched_at=new.robots_fetched_at""",
                {"h": host, "st": status, "p": rules.policy, "sha": sha, "cd": rules.crawl_delay},
            )
            self.db.commit()
        return rules
