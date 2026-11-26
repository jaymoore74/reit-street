"""Configuration.

Precedence (highest first):
  1. Environment variables  REITSTREET_<SECTION>_<KEY>  e.g. REITSTREET_DB_PASSWORD
  2. config.ini (path from --config, REITSTREET_CONFIG, or ./config.ini)
  3. Defaults below

Secrets (DB password, SMTP password, webhook URL) should come from environment
variables. config.ini is git-ignored, but env vars keep secrets off disk entirely.
Nothing secret is ever logged: Settings.redacted() is what gets printed.
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from urllib.parse import quote_plus

from .errors import ConfigError

UA_PLACEHOLDER = "YOUR NAME your.email@example.com"
SECRET_KEYS = {"db_password", "alerts_smtp_password", "alerts_webhook_url", "db_url"}
SECRET_PREFIXES = ("q4_feed_",)  # [q4_feed] keys land in Settings.extra


@dataclass
class Settings:
    # [sec]
    sec_user_agent: str = ""
    sec_requests_per_second: float = 8.0
    sec_download_exhibits: bool = True
    # [db]
    db_url: str = ""  # full SQLAlchemy URL; overrides the parts below when set
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str = "reit"
    db_password: str = ""
    db_name: str = "reitstreet"
    # CA bundle for TLS to the server (e.g. AWS RDS global-bundle.pem). When set, the
    # connection is encrypted and the server certificate and host name are verified.
    db_ssl_ca: Path | None = None
    # [paths]
    data_dir: Path = Path("data")
    raw_dir: Path | None = None  # defaults to <data_dir>/raw
    universe_csv: Path | None = None
    # [http] non-SEC sources (IR websites)
    http_user_agent: str = ""  # blank: generic ReitStreetBot UA (no personal details)
    http_requests_per_second: float = 1.0
    # [ir] IR website fetcher
    ir_requests_per_second: float = 0.5  # per host; robots.txt Crawl-delay wins when longer
    ir_download_kinds: str = "supplemental"  # comma list: supplemental, earnings_release, presentation
    ir_max_new_docs_per_company: int = 40
    ir_robots_ttl_hours: float = 24.0
    ir_headless: bool = False  # needs: pip install playwright && python -m playwright install chromium
    ir_supplemental_grace_days: int = 7
    ir_layout_break_min_links: int = 3
    ir_alert_after_failures: int = 3
    # [alerts]
    alerts_smtp_host: str = ""
    alerts_smtp_port: int = 587
    alerts_smtp_user: str = ""
    alerts_smtp_password: str = ""
    alerts_from: str = ""
    alerts_to: str = ""
    alerts_webhook_url: str = ""
    alerts_min_severity: str = "warn"
    # [monitor]
    monitor_max_hours_since_sync: float = 30.0
    monitor_stale_heartbeat_minutes: float = 30.0
    # [logging]
    logging_format: str = "json"  # json | text
    logging_level: str = "INFO"
    extra: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------ derived
    @property
    def raw_root(self) -> Path:
        return self.raw_dir or (self.data_dir / "raw")

    def sqlalchemy_url(self) -> str:
        if self.db_url:
            return self.db_url
        pw = quote_plus(self.db_password) if self.db_password else ""
        auth = f"{quote_plus(self.db_user)}:{pw}@" if pw else f"{quote_plus(self.db_user)}@"
        url = f"mysql+pymysql://{auth}{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"
        if self.db_ssl_ca:
            url += f"&ssl_ca={quote_plus(str(self.db_ssl_ca))}&ssl_check_hostname=true"
        return url

    def require_sec_user_agent(self) -> str:
        ua = (self.sec_user_agent or "").strip()
        if not ua or ua == UA_PLACEHOLDER or "example.com" in ua.lower() or "@" not in ua:
            raise ConfigError(
                "SEC requires a User-Agent with a real name and email. Set [sec] user_agent in config.ini "
                "(e.g. user_agent = Jane Smith jane@yourfirm.com) or REITSTREET_SEC_USER_AGENT."
            )
        return ua

    def web_user_agent(self, version: str) -> str:
        """User-Agent for IR websites. Honest and generic by default: it names the bot and its
        purpose, carries no personal name or email, and robots.txt rules address it as ReitStreetBot."""
        return (self.http_user_agent or "").strip() or (
            f"ReitStreetBot/{version} (REIT disclosure research; honors robots.txt)"
        )

    def ir_kinds(self) -> frozenset[str]:
        kinds = {k.strip() for k in self.ir_download_kinds.split(",") if k.strip()}
        bad = kinds - {"supplemental", "earnings_release", "presentation", "exhibit_other", "unknown"}
        if bad:
            raise ConfigError(f"[ir] download_kinds: unknown kinds {sorted(bad)}")
        return frozenset(kinds)

    def redacted(self) -> dict[str, object]:
        out: dict[str, object] = {}
        for f in fields(self):
            v = getattr(self, f.name)
            out[f.name] = "***" if (f.name in SECRET_KEYS and v) else (str(v) if isinstance(v, Path) else v)
        out["extra"] = {k: ("***" if k.startswith(SECRET_PREFIXES) and v else v) for k, v in self.extra.items()}
        return out


_TYPES = {f.name: f.type for f in fields(Settings)}


def _coerce(name: str, value: str, base: Path) -> object:
    t = str(_TYPES[name])
    if "bool" in t:
        return value.strip().lower() in ("1", "true", "yes", "on")
    if "int" in t and "float" not in t:
        return int(value)
    if "float" in t:
        return float(value)
    if "Path" in t:
        if not value.strip():
            return None
        p = Path(value.strip())
        return p if p.is_absolute() else (base / p)
    return value.strip()


def load(
    path: str | os.PathLike[str] | None = None, *, environ: dict[str, str] | None = None, require_file: bool = False
) -> Settings:
    env = os.environ if environ is None else environ
    cfg_path = Path(path or env.get("REITSTREET_CONFIG") or "config.ini")
    s = Settings()
    base = Path.cwd()
    if cfg_path.exists():
        cp = configparser.ConfigParser(interpolation=None)
        cp.read(cfg_path, encoding="utf-8")
        base = cfg_path.resolve().parent
        s.data_dir = base / "data"
        for section in cp.sections():
            for key, value in cp.items(section):
                name = f"{section}_{key}".replace("-", "_")
                if section == "paths":
                    name = key
                if name in _TYPES and name != "extra":
                    setattr(s, name, _coerce(name, value, base))
                else:
                    s.extra[name] = value
    elif require_file:
        raise ConfigError(f"Config file not found: {cfg_path}. Copy config/config.example.ini to config.ini.")
    for name in _TYPES:
        if name == "extra":
            continue
        v = env.get(f"REITSTREET_{name.upper()}")
        if v is not None:
            setattr(s, name, _coerce(name, v, base))
    s.sec_requests_per_second = max(0.1, min(float(s.sec_requests_per_second), 10.0))  # SEC hard cap
    return s
