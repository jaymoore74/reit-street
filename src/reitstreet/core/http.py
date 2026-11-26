"""HTTP fetching with rate limiting, retries and full capture.

Every successful response body goes into the raw store and gets a raw_fetch row
(URL, status, selected headers, timing, run). Failed requests get a raw_fetch row
too (with the error), so a broken source is visible in the database, not just logs.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from .db import Db
from .errors import FatalSourceError, TransientError
from .rawstore import RawStore

log = logging.getLogger(__name__)

KEEP_HEADERS = ("content-type", "last-modified", "etag", "content-length", "date")
RETRY_STATUS = {429, 500, 502, 503, 504}

# transport(url, headers) -> (status, headers, body)
Transport = Callable[[str, dict[str, str]], tuple[int, dict[str, str], bytes]]


class RateLimiter:
    """At most `rps` calls per second, shared across threads."""

    def __init__(self, rps: float):
        self.interval = 1.0 / max(0.01, rps)
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next:
                time.sleep(self._next - now)
                now = time.monotonic()
            self._next = now + self.interval


def requests_transport(timeout: float = 60.0) -> Transport:
    session = requests.Session()

    def _get(url: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
        r = session.get(url, headers=headers, timeout=timeout)
        return r.status_code, {k.lower(): v for k, v in r.headers.items()}, r.content

    return _get


def fixture_transport(root: Path) -> Transport:
    """Serve URLs from files at root/<host>/<path>. Used by tests and offline demos."""

    def _get(url: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
        p = urlparse(url)
        f = Path(root) / p.netloc / p.path.lstrip("/")
        if f.is_file():
            return 200, {"content-type": "application/octet-stream"}, f.read_bytes()
        return 404, {}, b"Not Found"

    return _get


@dataclass
class Fetched:
    url: str
    status: int
    body: bytes
    sha256: str
    fetch_id: int | None
    changed: bool  # content differs from the previous successful fetch of this URL

    def json(self) -> Any:
        return json.loads(self.body)

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class Fetcher:
    """One per source family (SEC, IR websites, ...), each with its own rate limit."""

    def __init__(
        self,
        *,
        source: str,
        user_agent: str,
        rps: float,
        store: RawStore,
        db: Db | None,
        run_id: int | None = None,
        transport: Transport | None = None,
        max_retries: int = 5,
        fatal_status: tuple[int, ...] = (),
        fatal_hint: str = "",
    ):
        self.source = source
        self.user_agent = user_agent
        self.limiter = RateLimiter(rps)
        self.store = store
        self.db = db
        self.run_id = run_id
        self.transport = transport or requests_transport()
        self.max_retries = max_retries
        self.fatal_status = fatal_status
        self.fatal_hint = fatal_hint
        self.request_count = 0
        self._sleep = time.sleep

    def headers(self) -> dict[str, str]:
        return {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"}

    def get(self, url: str, *, allow_404: bool = False) -> Fetched | None:
        delay = 1.0
        last_err = ""
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            self.request_count += 1
            t0 = time.monotonic()
            try:
                status, headers, body = self.transport(url, self.headers())
            except requests.RequestException as e:
                last_err = f"{type(e).__name__}: {e}"
                log.warning("network error, retrying", extra={"url": url, "attempt": attempt, "err": last_err})
                self._sleep(delay)
                delay = min(delay * 2, 60)
                continue
            ms = int((time.monotonic() - t0) * 1000)
            if status == 200:
                return self._record_ok(url, status, headers, body, ms)
            if status == 404 and allow_404:
                self._record_error(url, status, headers, ms, "404 not found")
                return None
            if status in self.fatal_status:
                self._record_error(url, status, headers, ms, f"HTTP {status}")
                raise FatalSourceError(f"HTTP {status} from {self.source} for {url}. {self.fatal_hint}".strip())
            if status in RETRY_STATUS and attempt < self.max_retries:
                ra = headers.get("retry-after", "")
                wait = float(ra) if ra.isdigit() else delay
                log.warning("retryable status", extra={"url": url, "status": status, "wait_s": wait})
                self._sleep(wait)
                delay = min(delay * 2, 60)
                continue
            self._record_error(url, status, headers, ms, f"HTTP {status}")
            raise TransientError(f"GET {url} -> HTTP {status}")
        self._record_error(url, None, {}, 0, last_err or "retries exhausted")
        raise TransientError(f"GET {url}: retries exhausted ({last_err})")

    # ---------------------------------------------------------------- capture
    def _record_ok(self, url: str, status: int, headers: dict[str, str], body: bytes, ms: int) -> Fetched:
        sha, rel = self.store.put(body)
        changed = True
        fetch_id = None
        if self.db is not None:
            prev = self.db.scalar(
                "SELECT sha256 FROM raw_fetch WHERE url_hash = SHA2(:u, 256) AND sha256 IS NOT NULL "
                "ORDER BY fetch_id DESC LIMIT 1",
                {"u": url},
            )
            changed = prev != sha
            register_object(self.db, sha, len(body), headers.get("content-type"), rel)
            fetch_id = self.db.execute(
                """INSERT INTO raw_fetch (run_id, source, url, http_status, sha256, response_headers, elapsed_ms)
                   VALUES (:run, :src, :url, :st, :sha, :hdr, :ms)""",
                {
                    "run": self.run_id,
                    "src": self.source,
                    "url": url,
                    "st": status,
                    "sha": sha,
                    "hdr": json.dumps({k: headers[k] for k in KEEP_HEADERS if k in headers}),
                    "ms": ms,
                },
            )
            self.db.commit()
        return Fetched(url, status, body, sha, fetch_id, changed)

    def _record_error(self, url: str, status: int | None, headers: dict[str, str], ms: int, err: str) -> None:
        if self.db is None:
            return
        self.db.execute(
            """INSERT INTO raw_fetch (run_id, source, url, http_status, sha256, response_headers, elapsed_ms, error)
               VALUES (:run, :src, :url, :st, NULL, :hdr, :ms, :err)""",
            {
                "run": self.run_id,
                "src": self.source,
                "url": url,
                "st": status,
                "hdr": json.dumps({k: headers[k] for k in KEEP_HEADERS if k in headers}),
                "ms": ms,
                "err": err[:2000],
            },
        )
        self.db.commit()


def register_object(db: Db, sha: str, nbytes: int, content_type: str | None, relpath: str) -> None:
    db.execute(
        """INSERT IGNORE INTO raw_object (sha256, bytes, content_type, storage_path)
           VALUES (:sha, :n, :ct, :p)""",
        {"sha": sha, "n": nbytes, "ct": (content_type or "")[:128] or None, "p": relpath},
    )


def store_local_file(
    db: Db, store: RawStore, path: Path, *, source: str, run_id: int | None, content_type: str | None = None
) -> tuple[str, int]:
    """Capture a file from disk (archive import, saved email) exactly like a fetch."""
    data = Path(path).read_bytes()
    sha, rel = store.put(data)
    register_object(db, sha, len(data), content_type, rel)
    fetch_id = db.execute(
        """INSERT INTO raw_fetch (run_id, source, url, http_status, sha256, response_headers, elapsed_ms)
           VALUES (:run, :src, :url, NULL, :sha, NULL, 0)""",
        {"run": run_id, "src": source, "url": Path(path).resolve().as_uri(), "sha": sha},
    )
    return sha, fetch_id
