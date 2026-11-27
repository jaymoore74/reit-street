from __future__ import annotations

import importlib.util
from pathlib import Path

from pymysql.converters import escape_string

ROOT = Path(__file__).resolve().parents[2]


def _bootstrap():  # deploy/ is not a package; load the script as a module
    spec = importlib.util.spec_from_file_location("bootstrap_db", ROOT / "deploy" / "aws" / "bootstrap_db.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_user_statements_survive_pymysql_formatting() -> None:
    # PyMySQL runs `query % escaped_args` when args are given; a bare '%' host broke the
    # first live bootstrap with "unsupported format character".
    b = _bootstrap()
    pw = "abc%def'ghi"
    rendered = [stmt % (f"'{escape_string(pw)}'",) for stmt in b.user_statements("rs_loader")]
    assert rendered == [
        "CREATE USER IF NOT EXISTS 'rs_loader'@'%' IDENTIFIED BY 'abc%def\\'ghi' REQUIRE SSL",
        "ALTER USER 'rs_loader'@'%' IDENTIFIED BY 'abc%def\\'ghi' REQUIRE SSL",
    ]
