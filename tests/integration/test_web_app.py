"""Client website schema (ADR 0013) and the `reitstreet web` admin commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from reitstreet.core import config as cfgmod
from reitstreet.core import migrate
from reitstreet.core.db import Db, make_engine
from reitstreet.core.errors import ConfigError
from reitstreet.web import admin
from tests.integration.helpers import cli

pytestmark = pytest.mark.mysql


@pytest.fixture
def app_url(env: Path, db_url: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """A fresh <test db>_app schema next to the test warehouse."""
    u = make_url(db_url)
    name = f"{u.database}_app"
    eng = create_engine(u.set(database=""), isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text(f"DROP DATABASE IF EXISTS `{name}`"))
        c.execute(text(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"))
    eng.dispose()
    monkeypatch.setenv("REITSTREET_DB_APP_NAME", name)
    return cfgmod.load().app_sqlalchemy_url()


@pytest.fixture
def app_db(app_url: str) -> Db:
    assert cli("migrate", "--target", "app") == 0
    d = Db(make_engine(app_url, isolation_level="READ COMMITTED"))
    yield d  # type: ignore[misc]
    d.close()


def test_app_migrations_up_down_up_and_comments(app_url: str) -> None:
    migrate.upgrade(app_url, target="app")
    db = Db(make_engine(app_url, isolation_level="READ COMMITTED"))
    try:
        n = db.scalar("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE()")
        assert n == 14  # 13 app_ tables and alembic_version
        uncommented = db.query(
            """SELECT CONCAT(c.table_name, '.', c.column_name) AS c FROM information_schema.columns c
               JOIN information_schema.tables t ON t.table_schema=c.table_schema AND t.table_name=c.table_name
               WHERE c.table_schema=DATABASE() AND t.table_type='BASE TABLE'
                 AND c.table_name <> 'alembic_version' AND c.column_comment = ''"""
        )
        assert [r["c"] for r in uncommented] == []
        names = [
            r["t"]
            for r in db.query(
                "SELECT table_name AS t FROM information_schema.tables WHERE table_schema=DATABASE() "
                "AND table_name <> 'alembic_version'"
            )
        ]
        assert all(t.startswith("app_") for t in names), names
        migrate.downgrade(app_url, "base", target="app")
        assert db.scalar("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE()") == 1
        migrate.upgrade(app_url, target="app")
        assert db.scalar("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE()") == n
        # the staff plan is seeded with everything
        kinds = {r["kind"] for r in db.query("SELECT kind FROM app_entitlement")}
        assert {"dataset", "format", "research", "coverage_all"} <= kinds
    finally:
        db.close()


def test_migrate_all_does_both(env: Path, app_url: str, db: Db) -> None:
    assert cli("migrate", "--target", "all", "--no-seed") == 0
    assert migrate.current_revision(app_url) == migrate.head_revision("app")
    assert (
        db.scalar(
            "SELECT COUNT(*) FROM information_schema.views "
            "WHERE table_schema=DATABASE() AND table_name='dl_metric_definitions'"
        )
        == 1
    )
    assert cli("migrate", "--target", "all", "--downgrade", "base") != 0  # refused: too broad


def test_app_data_dictionary_is_current(env: Path, app_db: Db) -> None:
    out = env / "dd-app.md"
    assert cli("db-docs", "--target", "app", "--out", str(out)) == 0
    committed = Path(__file__).resolve().parents[2] / "docs" / "data-dictionary-app.md"
    assert out.read_text(encoding="utf-8") == committed.read_text(encoding="utf-8"), (
        "web schema changed: run `reitstreet db-docs --target app --out docs/data-dictionary-app.md`"
    )


def test_admin_orgs_users_and_grants(app_db: Db) -> None:
    admin.plan_create(app_db, "basic", "Basic", None)
    admin.grant(app_db, plan="basic", kind="dataset", value="metrics", by="test")
    admin.org_create(app_db, "acme-capital", "Acme Capital", plan="basic", seats=1)
    with pytest.raises(ConfigError):
        admin.grant(app_db, plan="basic", kind="dataset", value="everything", by="test")
    with pytest.raises(ConfigError):
        admin.grant(app_db, plan="basic", kind="history_years", value="-1", by="test")

    link = admin.user_invite(app_db, "acme-capital", "Ann@Acme.example", name="Ann")
    assert link.email == "ann@acme.example"
    token = link.url.rsplit("/", 1)[1]
    row = app_db.one("SELECT * FROM app_user_token WHERE token_hash=:h", {"h": admin.token_hash(token)})
    assert row is not None and row["purpose"] == "invite" and row["used_at"] is None
    assert app_db.scalar("SELECT COUNT(*) FROM app_user_token WHERE token_hash=:t", {"t": token}) == 0  # never stored

    # re-inviting replaces the earlier link
    link2 = admin.user_invite(app_db, "acme-capital", "ann@acme.example")
    assert link2.url != link.url
    assert app_db.scalar("SELECT COUNT(*) FROM app_user_token WHERE used_at IS NULL AND purpose='invite'") == 1

    with pytest.raises(ConfigError, match="seats"):
        admin.user_invite(app_db, "acme-capital", "bob@acme.example")
    with pytest.raises(ConfigError, match="staff"):
        admin.user_invite(app_db, "acme-capital", "carl@acme.example", role="staff")

    # suspending the firm signs its users out
    uid = app_db.scalar("SELECT user_id FROM app_user WHERE email='ann@acme.example'")
    app_db.execute(
        "INSERT INTO app_session (session_hash, user_id, expires_at) "
        "VALUES (:h, :u, UTC_TIMESTAMP(6) + INTERVAL 1 DAY)",
        {"h": "a" * 64, "u": uid},
    )
    admin.org_set(app_db, "acme-capital", status="suspended")
    assert app_db.scalar("SELECT revoked_at IS NOT NULL FROM app_session WHERE session_hash=:h", {"h": "a" * 64}) == 1


def test_revoke_keeps_history(app_db: Db) -> None:
    admin.plan_create(app_db, "trial", "Trial", None)
    gid = admin.grant(app_db, plan="trial", kind="coverage_ticker", value="pld", by="test")
    assert app_db.scalar("SELECT value FROM app_entitlement WHERE entitlement_id=:i", {"i": gid}) == "PLD"
    admin.revoke(app_db, gid)
    row = app_db.one("SELECT valid_to FROM app_entitlement WHERE entitlement_id=:i", {"i": gid})
    assert row is not None and row["valid_to"] is not None


def test_article_versions(app_db: Db, tmp_path: Path) -> None:
    f = tmp_path / "note.md"
    f.write_text("---\nslug: test-note\ntitle: Test\nstatus: draft\n---\nFirst.\n", encoding="utf-8")
    assert admin.article_save(app_db, f, by="test")["version"] == 1
    assert admin.article_save(app_db, f, by="test")["changed"] is False
    f.write_text("---\nslug: test-note\ntitle: Test\nstatus: published\n---\nSecond.\n", encoding="utf-8")
    res = admin.article_save(app_db, f, by="test")
    assert res == {"slug": "test-note", "version": 2, "changed": True, "status": "published"}
    assert app_db.scalar("SELECT COUNT(*) FROM app_research_article_version") == 2
    assert app_db.scalar("SELECT published_at IS NOT NULL FROM app_research_article") == 1
    f.write_text("no front matter", encoding="utf-8")
    with pytest.raises(ConfigError):
        admin.article_save(app_db, f)


def test_web_cli(app_db: Db, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        cli(
            "web",
            "--by",
            "test",
            "org-create",
            "--slug",
            "our-firm",
            "--name",
            "Our Firm",
            "--plan",
            "staff",
            "--internal",
        )
        == 0
    )
    capsys.readouterr()
    assert (
        cli(
            "web",
            "--by",
            "test",
            "user-invite",
            "--org",
            "our-firm",
            "--email",
            "me@ours.example",
            "--role",
            "staff_admin",
            "--base-url",
            "https://site.example",
        )
        == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert out["link"].startswith("https://site.example/invite/")
    assert cli("web", "--by", "test", "list") == 0
    listing = json.loads(capsys.readouterr().out)
    assert [o["slug"] for o in listing["organizations"]] == ["our-firm"]
