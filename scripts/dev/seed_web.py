"""Build a local development database for the client website from SYNTHETIC fixtures.

Runs the real pipeline (migrate, universe, sec-sync) against the offline SEC fixtures used
by the tests (tests/sec_fixtures.py: 12 REITs, synthetic values), then sets up the web
schema with a demo client, a staff user and a sample research note, and prints single-use
invitation links. Never point this at a production database: it refuses unless the
database names end in _dev or _test.

Run from the repo root, with a local MySQL 8 and a login that may create databases:
  set REITSTREET_DB_URL=mysql+pymysql://root:pw@127.0.0.1:3306/reitstreet_dev
  set REITSTREET_DB_APP_NAME=reitstreet_app_dev
  python scripts/dev/seed_web.py
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from tests.sec_fixtures import build  # noqa: E402

from reitstreet.cli import main as cli  # noqa: E402
from reitstreet.core import config as cfgmod  # noqa: E402

SAMPLE_ARTICLE = ROOT / "apps" / "web" / "sample" / "research" / "sample-note.md"


def _recreate(url: str) -> None:
    u = make_url(url)
    eng = create_engine(u.set(database=""), isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text(f"DROP DATABASE IF EXISTS `{u.database}`"))
        c.execute(text(f"CREATE DATABASE `{u.database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"))
    eng.dispose()


def run(*args: str) -> None:
    rc = cli(["--log-format", "text", *args])
    if rc != 0:
        raise SystemExit(f"reitstreet {' '.join(args)} failed with exit code {rc}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base-url", default="http://localhost:3000")
    p.add_argument("--keep", action="store_true", help="do not drop and recreate the two databases")
    p.add_argument("--links-file", type=Path, help="also write the invitation links to this JSON file (for tests)")
    a = p.parse_args()

    s = cfgmod.load()
    wh, app = make_url(s.sqlalchemy_url()), make_url(s.app_sqlalchemy_url())
    for u in (wh, app):
        if not str(u.database).endswith(("_dev", "_test")):
            print(f"refusing: database {u.database!r} does not end in _dev or _test", file=sys.stderr)
            return 2
    if not a.keep:
        _recreate(s.sqlalchemy_url())
        _recreate(s.app_sqlalchemy_url())

    with tempfile.TemporaryDirectory() as tmp:
        root = build(Path(tmp) / "sec")
        run("migrate", "--target", "all")
        run("universe", "--csv", str(root / "reits.csv"))
        run("sec-sync", "--offline-fixtures", str(root), "--full-history")

    run("web", "--by", "dev-seed", "plan-create", "--code", "demo", "--name", "Demo plan (placeholder)")
    for kind, value in [
        ("dataset", "companies"),
        ("dataset", "metrics"),
        ("dataset", "filings"),
        ("research", "core"),
        ("format", "csv"),
        ("format", "xlsx"),
        # 6 of the 12 fixture REITs, so the site shows what a partial subscription hides
        *[("coverage_ticker", t) for t in ("PLD", "O", "SPG", "EQR", "WELL", "DLR")],
        ("history_years", "5"),
    ]:
        run("web", "--by", "dev-seed", "grant", "--plan", "demo", "--kind", kind, "--value", value)
    run("web", "--by", "dev-seed", "org-create", "--slug", "demo-capital", "--name", "Demo Capital", "--plan", "demo")
    run(
        "web",
        "--by",
        "dev-seed",
        "org-create",
        "--slug",
        "reit-street",
        "--name",
        "REIT Street",
        "--plan",
        "staff",
        "--internal",
    )
    run("web", "--by", "dev-seed", "article", str(SAMPLE_ARTICLE), "--status", "published")
    print("\nInvitation links (single use, 7 days):")
    links: dict[str, str] = {}
    for org, email, name, role in (
        ("demo-capital", "client@demo.invalid", "Demo Client", "member"),
        ("reit-street", "staff@demo.invalid", "Staff", "staff_admin"),
    ):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            run(
                "web",
                "user-invite",
                "--org",
                org,
                "--email",
                email,
                "--name",
                name,
                "--role",
                role,
                "--base-url",
                a.base_url,
            )
        print(buf.getvalue(), end="")
        links[email] = json.loads(buf.getvalue())["link"]
    if a.links_file:
        a.links_file.write_text(json.dumps(links, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
