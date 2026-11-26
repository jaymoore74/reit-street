from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from reitstreet.core import config as cfgmod
from reitstreet.core.errors import ConfigError, FatalSourceError, TransientError
from reitstreet.core.http import Fetcher, RateLimiter
from reitstreet.core.rawstore import IntegrityError, RawStore, sha256_of


def test_config_precedence(tmp_path: Path) -> None:
    ini = tmp_path / "config.ini"
    ini.write_text(
        "[sec]\nuser_agent = Ini User ini@x.org\nrequests_per_second = 50\n[db]\nname = from_ini\n"
        "[paths]\ndata_dir = d\n"
    )
    s = cfgmod.load(ini, environ={"REITSTREET_DB_NAME": "from_env"})
    assert s.db_name == "from_env"  # env beats file
    assert s.sec_user_agent == "Ini User ini@x.org"
    assert s.sec_requests_per_second == 10.0  # SEC cap
    assert s.data_dir == tmp_path / "d"  # relative to the config file
    assert s.raw_root == tmp_path / "d" / "raw"


def test_config_secrets_redacted() -> None:
    s = cfgmod.load("missing.ini", environ={"REITSTREET_DB_PASSWORD": "hunter2"})
    assert s.redacted()["db_password"] == "***"
    assert "hunter2" in s.sqlalchemy_url()
    assert "hunter2" not in str(s.redacted())


@pytest.mark.parametrize("ua", ["", cfgmod.UA_PLACEHOLDER, "Jane jane@example.com", "Jane Smith"])
def test_user_agent_must_be_real(ua: str) -> None:
    s = cfgmod.load("missing.ini", environ={"REITSTREET_SEC_USER_AGENT": ua})
    with pytest.raises(ConfigError):
        s.require_sec_user_agent()


def test_require_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        cfgmod.load(tmp_path / "nope.ini", environ={}, require_file=True)


def test_rawstore_roundtrip_and_integrity(tmp_path: Path) -> None:
    store = RawStore.local(tmp_path)
    sha, rel = store.put(b"hello")
    assert sha == sha256_of(b"hello")
    assert rel == f"sha256/{sha[:2]}/{sha[2:4]}/{sha}.gz"
    assert store.put(b"hello") == (sha, rel)  # idempotent
    assert store.get(sha) == b"hello"
    # the sha is of the uncompressed bytes; gzip headers may differ across Python versions
    assert gzip.decompress((tmp_path / rel).read_bytes()) == b"hello"
    (tmp_path / rel).write_bytes(gzip.compress(b"tampered"))
    with pytest.raises(IntegrityError):
        store.get(sha)
    with pytest.raises(FileNotFoundError):
        store.get("0" * 64)


def test_rate_limiter_spaces_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    slept: list[float] = []
    monkeypatch.setattr("reitstreet.core.http.time.monotonic", lambda: clock[0])

    def sleep(s: float) -> None:
        slept.append(s)
        clock[0] += s

    monkeypatch.setattr("reitstreet.core.http.time.sleep", sleep)
    rl = RateLimiter(4)
    for _ in range(5):
        rl.wait()
    assert clock[0] == pytest.approx(1.0)  # 5 calls at 4/s take 1 second
    assert all(s == pytest.approx(0.25) for s in slept)


def _fetcher(tmp_path: Path, responses: list[tuple[int, dict[str, str], bytes]], **kw: object) -> Fetcher:
    it = iter(responses)
    f = Fetcher(
        source="sec",
        user_agent="T t@x.org",
        rps=1000,
        store=RawStore.local(tmp_path),
        db=None,
        transport=lambda url, headers: next(it),
        **kw,
    )  # type: ignore[arg-type]
    f._sleep = lambda s: None
    return f


def test_fetcher_retries_then_succeeds(tmp_path: Path) -> None:
    f = _fetcher(tmp_path, [(503, {}, b""), (429, {"retry-after": "2"}, b""), (200, {}, b"ok")])
    r = f.get("https://x/y")
    assert r is not None and r.body == b"ok" and f.request_count == 3
    assert RawStore.local(tmp_path).get(r.sha256) == b"ok"


def test_fetcher_fatal_and_transient(tmp_path: Path) -> None:
    with pytest.raises(FatalSourceError):
        _fetcher(tmp_path, [(403, {}, b"")], fatal_status=(403,)).get("https://x")
    with pytest.raises(TransientError):
        _fetcher(tmp_path, [(500, {}, b"")] * 3, max_retries=2).get("https://x")
    assert _fetcher(tmp_path, [(404, {}, b"")]).get("https://x", allow_404=True) is None
    with pytest.raises(TransientError):
        _fetcher(tmp_path, [(404, {}, b"")]).get("https://x")
