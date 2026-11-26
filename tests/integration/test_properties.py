"""Property master end to end: 10-K Schedule III from the SEC fixtures, a manual list,
matching to stable ids, ownership spells, the review queue and the property DQ checks."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from reitstreet.core.db import Db
from tests.integration.helpers import bootstrap, cli, last_run

pytestmark = pytest.mark.mysql


def _load(sec_root: Path, *extra: str) -> None:
    assert (
        cli("load-properties", "--offline-fixtures", str(sec_root), "--tickers", "PLD,O,SPG", "--years", "2",
            "--sources", "sec_schedule_iii", *extra)
        == 0
    )  # fmt: skip


def _prop(db: Db, ticker: str, name: str) -> int:
    pid = db.scalar(
        """SELECT DISTINCT x.property_id FROM wh_property_xref x
           JOIN stg_property_record r ON r.record_id = x.record_id JOIN ref_company c ON c.company_id = r.company_id
           WHERE x.retired_at IS NULL AND r.retired_at IS NULL AND c.ticker = :t AND r.name = :n""",
        {"t": ticker, "n": name},
    )
    assert pid is not None, (ticker, name)
    return int(pid)


def _spells(db: Db, pid: int) -> list[dict]:
    return db.query(
        """SELECT c.ticker, o.* FROM wh_property_ownership o JOIN ref_company c ON c.company_id = o.company_id
           WHERE o.property_id = :p AND o.retired_at IS NULL ORDER BY c.ticker, o.spell_no""",
        {"p": pid},
    )


def _dq(db: Db) -> dict[str, str]:
    run = last_run(db, "dq")["run_id"]
    return {
        r["check_code"]: r["status"]
        for r in db.query(
            "SELECT check_code, status FROM ops_dq_result WHERE run_id=:r AND subject='summary'", {"r": run}
        )
    }


def test_seed_loads_types_and_source_map(env: Path, db: Db) -> None:
    assert cli("migrate") == 0
    assert cli("universe") == 0
    assert db.scalar("SELECT COUNT(*) FROM ref_property_type") >= 30
    assert db.scalar("SELECT COUNT(*) FROM ref_property_type_map") >= 50
    # the fixture universe holds a few real tickers: their rows of the source map attach
    assert db.scalar("SELECT COUNT(*) FROM ref_property_source") > 0


def test_schedule_iii_to_property_master(env: Path, sec_root: Path, db: Db) -> None:
    bootstrap(sec_root)
    _load(sec_root)
    run = last_run(db, "load-properties")
    assert run["status"] == "succeeded", run

    # PLD: two 10-Ks x (2 properties + 1 aggregate row + 1 total); O: one 10-K; SPG: no Schedule III in its filing
    n = {
        r["ticker"]: int(r["n"])
        for r in db.query(
            """SELECT c.ticker, COUNT(*) AS n FROM stg_property_record r JOIN ref_company c USING (company_id)
               WHERE r.retired_at IS NULL GROUP BY c.ticker"""
        )
    }
    assert n == {"PLD": 8, "O": 4}
    assert db.scalar("SELECT COUNT(*) FROM stg_property_record WHERE is_aggregate = 1") == 6
    assert db.scalar("SELECT COUNT(*) FROM wh_property") == 5  # aggregates never become properties

    oak = _prop(db, "PLD", "Oak Park Center")
    p = db.one("SELECT * FROM wh_property WHERE property_id = :p", {"p": oak})
    assert p is not None
    assert (p["city"], p["state"], p["status"], p["n_records"], p["year_built"]) == ("Dallas", "TX", "active", 2, 1998)
    assert (p["first_seen"], p["last_seen"]) == (date(2023, 12, 31), date(2024, 12, 31))
    methods = sorted(
        r["method"] for r in db.query("SELECT method FROM wh_property_xref WHERE property_id = :p", {"p": oak})
    )
    assert methods == ["new", "source_ref"]
    (sp,) = _spells(db, oak)
    assert (sp["ticker"], sp["acquired_date"], sp["acquired_basis"], sp["disposed_date"]) == (
        "PLD",
        date(2016, 3, 15),
        "reported",
        None,
    )

    # Lakeview Plaza is in the FY2023 10-K only: sold, inferred from the complete FY2024 list
    lake = _prop(db, "PLD", "Lakeview Plaza")
    (sp,) = _spells(db, lake)
    assert (sp["disposed_date"], sp["disposed_basis"]) == (date(2024, 12, 31), "not_listed")
    assert db.scalar("SELECT status FROM wh_property WHERE property_id = :p", {"p": lake}) == "inactive"

    # the same names at another REIT, with no address or point, stay separate properties
    assert _prop(db, "O", "Oak Park Center") != oak
    assert db.scalar("SELECT COUNT(*) FROM dl_properties") == 4
    assert db.scalar("SELECT COUNT(*) FROM dl_properties WHERE owner_ticker = 'PLD'") == 2

    # idempotent: reload and rebuild change nothing
    before = (
        db.scalar("SELECT COUNT(*) FROM stg_property_record"),
        db.scalar("SELECT COUNT(*) FROM wh_property_xref"),
        db.scalar("SELECT COUNT(*) FROM wh_property_ownership"),
        db.scalar("SELECT COUNT(*) FROM wh_property_alias"),
        db.scalar("SELECT GROUP_CONCAT(attr_hash ORDER BY property_id) FROM wh_property"),
    )
    _load(sec_root)
    assert cli("build-properties") == 0
    assert cli("build-properties", "--rematch") == 0
    after = (
        db.scalar("SELECT COUNT(*) FROM stg_property_record"),
        db.scalar("SELECT COUNT(*) FROM wh_property_xref WHERE retired_at IS NULL"),
        db.scalar("SELECT COUNT(*) FROM wh_property_ownership"),
        db.scalar("SELECT COUNT(*) FROM wh_property_alias"),
        db.scalar("SELECT GROUP_CONCAT(attr_hash ORDER BY property_id) FROM wh_property"),
    )
    assert after == before
    assert db.scalar("SELECT COUNT(*) FROM wh_property") == 5  # rematch kept every id

    # a reload with --force restages the same rows: same keys, nothing new
    _load(sec_root, "--force")
    assert db.scalar("SELECT COUNT(*) FROM stg_property_record") == before[0]

    assert cli("dq", "--only", "recon.schedule_iii,contract.property,anomaly.property,ops.property") == 0
    res = _dq(db)
    assert res["recon.schedule_iii_total"] == "pass", res
    assert res["contract.property_owner"] == "pass", res
    assert res["contract.property_records_placed"] == "pass", res
    assert res["anomaly.property_overowned"] == "pass", res

    out = env / "coverage.csv"
    assert cli("property-coverage", "--out", str(out)) == 0
    rows = {r["ticker"]: r for r in csv.DictReader(out.open(encoding="utf-8"))}
    assert rows["PLD"]["properties"] == "2" and rows["O"]["properties"] == "2"


def test_import_review_and_merge(env: Path, sec_root: Path, db: Db, tmp_path: Path) -> None:
    bootstrap(sec_root)
    _load(sec_root)
    oak = _prop(db, "PLD", "Oak Park Center")

    f1 = tmp_path / "pld_list.csv"
    f1.write_text(
        "Property Name,Address,City,State,Zip,Square Feet\n"
        'Oak Park Center,100 Main Street,Dallas,TX,75201,"250,000"\n'
        "Harbor Point,9 Dock Rd,Tampa,FL,33602,80000\n",
        encoding="utf-8",
    )
    assert cli("import-properties", str(f1), "--ticker", "PLD", "--as-of", "2025-06-30") == 0
    how = db.scalar(
        """SELECT method FROM wh_property_xref x JOIN stg_property_record r USING (record_id)
           WHERE r.source_kind='manual' AND r.name='Oak Park Center' AND x.retired_at IS NULL"""
    )
    assert how == "name_city"
    p = db.one("SELECT * FROM wh_property WHERE property_id = :p", {"p": oak})
    assert p is not None
    # manual beats Schedule III for canonical attributes
    assert p["address_line"] == "100 Main Street" and int(p["rentable_sf"]) == 250000 and p["postal_code"] == "75201"
    assert p["n_source_kinds"] == 2
    harbor = _prop(db, "PLD", "Harbor Point")

    # a close name in another list goes to review on a provisional property
    f2 = tmp_path / "pld_list_q3.csv"
    f2.write_text("Property Name,City,State\nOak Park Ctr,Dallas,TX\n", encoding="utf-8")
    assert cli("import-properties", str(f2), "--ticker", "PLD", "--as-of", "2025-09-30") == 0
    q = db.one(
        """SELECT x.record_id, x.property_id, x.candidates FROM wh_property_xref x
           JOIN stg_property_record r USING (record_id)
           WHERE r.name = 'Oak Park Ctr' AND x.retired_at IS NULL AND x.review_status = 'needs_review'"""
    )
    assert q is not None and q["property_id"] != oak
    cands = json.loads(q["candidates"]) if isinstance(q["candidates"], str) else q["candidates"]
    assert cands[0]["property_id"] == oak
    assert cli("dq", "--only", "ops.property") == 0
    assert _dq(db)["ops.property_review_queue"] != "pass"

    assert cli("property-review", "list") == 0
    assert cli("property-review", "confirm", "--record", str(q["record_id"]), "--property", str(oak)) == 0
    prov = db.one(
        "SELECT status, merged_into_property_id FROM wh_property WHERE property_id = :p", {"p": q["property_id"]}
    )
    assert prov is not None and (prov["status"], prov["merged_into_property_id"]) == ("merged", oak)
    assert (
        db.scalar(
            "SELECT review_status FROM wh_property_xref WHERE record_id = :r AND retired_at IS NULL",
            {"r": q["record_id"]},
        )
        == "confirmed"
    )
    # a manual link survives a rematch
    assert cli("build-properties", "--rematch") == 0
    assert (
        db.scalar(
            "SELECT property_id FROM wh_property_xref WHERE record_id = :r AND retired_at IS NULL",
            {"r": q["record_id"]},
        )
        == oak
    )

    # merge: every record moves, the old id points at the survivor
    assert cli("property-review", "merge", "--from", str(harbor), "--property", str(oak)) == 0
    assert db.scalar("SELECT merged_into_property_id FROM wh_property WHERE property_id = :p", {"p": harbor}) == oak
    assert (
        db.scalar("SELECT COUNT(*) FROM wh_property_xref WHERE property_id = :p AND retired_at IS NULL", {"p": harbor})
        == 0
    )
    assert (
        db.scalar(
            "SELECT COUNT(*) FROM wh_property_ownership WHERE property_id = :p AND retired_at IS NULL", {"p": harbor}
        )
        == 0
    )

    # bad ids are refused, not crashed on
    assert cli("property-review", "confirm", "--record", str(q["record_id"]), "--property", str(harbor)) != 0
    assert cli("property-review", "merge", "--from", str(oak), "--property", str(oak)) != 0


def test_overowned_and_unplaced_checks(env: Path, sec_root: Path, db: Db) -> None:
    bootstrap(sec_root)
    _load(sec_root)
    # two REITs both own the same property in full: the check must flag it
    assert cli("property-review", "merge", "--from", str(_prop(db, "O", "Riverside Apartments")),
               "--property", str(_prop(db, "PLD", "Riverside Apartments"))) == 0  # fmt: skip
    db.execute(
        """DELETE FROM wh_property_xref
           WHERE record_id = (SELECT MIN(record_id) FROM stg_property_record WHERE is_aggregate = 0)"""
    )
    db.commit()
    assert cli("dq", "--only", "anomaly.property,contract.property") == 0
    res = _dq(db)
    assert res["anomaly.property_overowned"] == "fail", res
    assert res["contract.property_records_placed"] == "fail", res
