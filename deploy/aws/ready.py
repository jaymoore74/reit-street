"""Readiness gate for scheduled runs: is the schema at head and the universe loaded?

Prints ready=true|false to $GITHUB_OUTPUT (or stdout). Exits 0 either way unless the
database cannot be reached at all, so scheduled jobs stay quiet until the first load
is done and fail loudly if the database is down.
"""

from __future__ import annotations

import os
import sys

from reitstreet.core import config, migrate
from reitstreet.core.db import Db


def main() -> int:
    s = config.load()
    url = s.sqlalchemy_url()
    rev, head = migrate.current_revision(url), migrate.head_revision()
    companies = 0
    if rev == head:
        db = Db.from_url(url)
        try:
            companies = int(db.scalar("SELECT COUNT(*) FROM ref_company WHERE in_universe = 1") or 0)
        finally:
            db.close()
    ready = rev == head and companies > 0
    msg = f"schema {rev} (head {head}), companies in universe {companies}"
    print(msg if ready else f"::notice::Not ready for scheduled runs, skipping: {msg}. Run ops-first-load first.")
    out = os.environ.get("GITHUB_OUTPUT")
    line = f"ready={'true' if ready else 'false'}\n"
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(line)
    else:
        sys.stdout.write(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
