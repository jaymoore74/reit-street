"""Supplemental PDFs end to end: archive import, parse, record tables, metrics, profile, DQ, export."""

from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path

import pytest

from reitstreet.core.db import Db
from tests.integration.helpers import bootstrap, cli, last_run, metric
from tests.pdf import samples

pytestmark = pytest.mark.mysql

Q3 = "2024-09-30"


def _dq(db: Db) -> dict[str, str]:
    run = last_run(db, "dq")["run_id"]
    return {
        r["check_code"]: r["status"]
        for r in db.query(
            "SELECT check_code, status FROM ops_dq_result WHERE run_id=:r AND subject='summary'", {"r": run}
        )
    }


def _load(env: Path, sec_root: Path) -> Path:
    bootstrap(sec_root)
    arch = env / "archive" / "PLD"
    arch.mkdir(parents=True)
    (arch / "PLD_2024Q3_supplemental.pdf").write_bytes(samples.combined(2024))
    assert cli("import-archive", str(arch.parent)) == 0
    assert cli("parse-docs") == 0
    return arch


def _count(db: Db, table: str, where: str = "is_live = 1") -> int:
    return int(db.scalar(f"SELECT COUNT(*) FROM {table} WHERE {where}") or 0)


def test_pdf_supplemental_to_record_tables_and_metrics(env: Path, sec_root: Path, db: Db) -> None:
    _load(env, sec_root)
    doc = db.one("SELECT doc_id, status, current_parse_id FROM stg_document WHERE original_filename LIKE 'PLD_2024Q3%'")
    assert doc and doc["status"] == "parsed"
    parse = db.one("SELECT * FROM stg_doc_parse WHERE parse_id = :p", {"p": doc["current_parse_id"]})
    assert parse and parse["parser_name"] == "supplemental_pdf" and parse["n_records"] == 17
    assert parse["n_quarantined"] == 4 and parse["config_hash"]
    kinds = {
        r["table_kind"]
        for r in db.query("SELECT table_kind FROM stg_doc_table WHERE parse_id=:p", {"p": parse["parse_id"]})
    }
    assert {"ffo", "debt_maturity", "tenant", "lease_expiration", "same_store", "property", "noi"} <= kinds

    # company-level metrics from the series tables
    ffo = metric(db, "PLD", "FFO_NAREIT", "Q", Q3, source="supplemental")
    assert [r["value"] for r in ffo] == [Decimal("222778000")]
    assert ffo[0]["source_page_no"] == 1 and ffo[0]["source_item_id"]
    assert [r["value"] for r in metric(db, "PLD", "FFO_PER_SHARE", "Q", Q3, "supplemental")] == [Decimal("1.21")]
    assert [r["value"] for r in metric(db, "PLD", "SS_NOI_GROWTH", "Q", Q3, "supplemental")] == [Decimal("3.4")]
    # quarantined values (no scale on page 4) never reach the warehouse
    assert not metric(db, "PLD", "NOI", "Q", Q3, "supplemental")

    # record tables, each row tied to file hash, page, table and record
    # one row per bucket and debt type: 2025 (secured, total), 2026 (secured, unsecured, total),
    # Thereafter (unsecured, total), Total (secured, unsecured, total)
    assert _count(db, "wh_debt_maturity") == 10
    assert _count(db, "wh_debt_maturity", "is_live = 1 AND debt_type = 'Total'") == 4
    total = db.one("SELECT * FROM wh_debt_maturity WHERE is_live = 1 AND is_total = 1 AND debt_type = 'Total'")
    assert total and total["amount"] == Decimal("2243449000.00") and total["maturity_year"] is None
    later = db.one(
        "SELECT * FROM wh_debt_maturity WHERE is_live = 1 AND bucket_label = 'Thereafter' AND debt_type = 'Total'"
    )
    assert later and later["maturity_year"] is None and later["amount"] == Decimal("1800000000.00")
    for table in ("wh_debt_maturity", "wh_tenant_exposure", "wh_lease_expiration", "wh_property_metric"):
        missing = _count(
            db,
            table,
            "is_live = 1 AND (source_sha256 IS NULL OR source_table_id IS NULL "
            "OR source_record_id IS NULL OR source_page_no IS NULL)",
        )
        assert missing == 0, table
    assert _count(db, "wh_tenant_exposure") == 5
    top = db.one("SELECT * FROM wh_tenant_exposure WHERE is_live = 1 AND tenant_name = '7-Eleven'")
    assert top and top["annualized_rent"] == Decimal("48210000.00") and top["rent_pct"] == Decimal("5.6000")
    assert _count(db, "wh_lease_expiration") == 5
    # property facts: 3 communities x (units, occupancy), not linked to the property master yet
    grove = db.query(
        """SELECT m.metric_code, p.value, p.property_record_id, p.city FROM wh_property_metric p
           JOIN ref_metric m USING (metric_id) WHERE p.is_live = 1 AND p.property_name = 'The Grove'"""
    )
    assert {r["metric_code"]: r["value"] for r in grove} == {
        "PROP_UNITS": Decimal("412"),
        "PROP_OCCUPANCY": Decimal("96.1"),
    }
    assert all(r["city"] == "Austin" for r in grove)
    # each property row is staged for the property master, and the figures key to that record
    staged = db.query(
        """SELECT record_id, name, state, unit_count, unit_kind, occupancy_pct, source_page_no, source_sha256
           FROM stg_property_record WHERE source_kind = 'supplemental' AND retired_at IS NULL ORDER BY name"""
    )
    assert [r["name"] for r in staged] == ["Elm Street Lofts", "Harbor Point", "The Grove"]
    g = staged[-1]
    assert g["state"] == "TX" and g["unit_count"] == Decimal("412.00") and g["unit_kind"] == "units"
    assert g["occupancy_pct"] == Decimal("96.1000") and g["source_page_no"] == 3 and g["source_sha256"]
    assert {r["property_record_id"] for r in grove} == {g["record_id"]}
    assert _count(db, "wh_property", "1=1") == 0  # matching is the property master's job, not the parser's

    # quarantine is visible for review
    q = db.query("SELECT * FROM ops_parse_quarantine")
    assert len(q) == 4 and all("scale unknown" in (r["quarantine_reason"] or "") for r in q)


def test_property_figures_resolve_through_the_property_master(env: Path, sec_root: Path, db: Db) -> None:
    _load(env, sec_root)
    assert not [r for r in db.query("SELECT property_id FROM dl_property_metrics") if r["property_id"]]
    assert cli("build-properties") == 0
    rows = db.query("SELECT property_id, property_name FROM dl_property_metrics WHERE ticker = 'PLD'")
    assert len(rows) == 6 and all(r["property_id"] for r in rows)
    assert len({r["property_id"] for r in rows}) == 3
    # a rebuild of the record tables does not disturb the link or the figures
    before = _count(db, "wh_property_metric", "1=1")
    assert cli("build-metrics", "--tickers", "PLD") == 0
    assert _count(db, "wh_property_metric", "1=1") == before
    assert _count(db, "stg_property_record", "source_kind = 'supplemental'") == 3
    again = db.query("SELECT property_id FROM dl_property_metrics WHERE ticker = 'PLD'")
    assert {r["property_id"] for r in again} == {r["property_id"] for r in rows}


def test_rebuild_is_idempotent_and_reparse_versions(env: Path, sec_root: Path, db: Db) -> None:
    arch = _load(env, sec_root)
    before = {t: _count(db, t, "1=1") for t in ("wh_debt_maturity", "wh_tenant_exposure", "wh_property_metric")}
    assert cli("build-metrics", "--tickers", "PLD") == 0
    after = {t: _count(db, t, "1=1") for t in before}
    assert before == after
    assert _count(db, "wh_debt_maturity", "version > 1") == 0

    # a later quarter's file restates nothing for Q3 but adds its own schedule
    (arch / "PLD_2024Q4_supplemental.pdf").write_bytes(samples.ffo_and_debt(2024))
    assert cli("import-archive", str(arch.parent)) == 0
    assert cli("parse-docs") == 0
    dates = {str(r["as_of_date"]) for r in db.query("SELECT DISTINCT as_of_date FROM wh_debt_maturity WHERE is_live=1")}
    assert dates == {Q3, "2024-12-31"}
    latest = db.query("SELECT * FROM dl_debt_maturities WHERE ticker = 'PLD'")
    assert latest and {str(r["as_of_date"]) for r in latest} == {"2024-12-31"}


def test_profile_catalog_coverage_dq_and_export(
    env: Path, sec_root: Path, db: Db, capsys: pytest.CaptureFixture[str]
) -> None:
    _load(env, sec_root)
    # sector catalog seeded and the fixture universe (all "Industrial") mapped
    assert _count(db, "ref_sector", "1=1") >= 30
    assert (
        db.scalar(
            "SELECT sector_code FROM ref_company_sector cs JOIN ref_company c USING (company_id) WHERE c.ticker='PLD'"
        )
        == "industrial"
    )
    prof = db.query("SELECT * FROM dl_company_profile WHERE ticker = 'PLD'")
    codes = {r["metric_code"]: r for r in prof}
    assert codes["FFO_NAREIT"]["has_value"] == 1 and codes["FFO_NAREIT"]["source_page_no"] == 1
    assert codes["TOTAL_REVENUE"]["has_value"] == 1  # XBRL values show up too
    assert "REVPAR" not in codes  # a hotel metric is not relevant to an industrial REIT

    capsys.readouterr()
    assert cli("company-profile", "--ticker", "PLD", "--json") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["company"]["ticker"] == "PLD" and out["coverage"]["with_value"] >= 5
    assert len(out["debt_maturities"]) == 10 and len(out["top_tenants"]) == 5
    assert out["properties"]["count"] == 3 and out["properties"]["matched_to_property_master"] == 0
    assert cli("company-profile", "--ticker", "PLD") == 0
    assert "FFO_NAREIT" in capsys.readouterr().out
    assert cli("company-profile", "--ticker", "NOPE") == 2

    assert cli("parse-coverage", "--out", str(env / "cov.csv")) == 0
    summary = json.loads(capsys.readouterr().out.splitlines()[0])
    assert summary["with_parsed_supplemental"] == 1 and summary["with_debt_maturities"] == 1

    assert cli("dq", "--only", "contract.record,contract.company,anomaly.record,anomaly.schedule,ops.parse") == 0
    res = _dq(db)
    for code in (
        "contract.record_lineage",
        "contract.record_single_current",
        "contract.company_sectors",
        "anomaly.record_pct_range",
        "anomaly.schedule_shares",
        "ops.parse_quarantine_rate",
    ):
        assert res[code] == "pass", (code, res)
    db.execute("UPDATE wh_tenant_exposure SET source_sha256 = NULL, rent_pct = 140 WHERE tenant_name = '7-Eleven'")
    db.commit()
    assert cli("dq", "--only", "contract.record,anomaly") == 0
    res = _dq(db)
    assert res["contract.record_lineage"] == "fail" and res["anomaly.record_pct_range"] == "fail"
    assert res["anomaly.schedule_shares"] == "fail"

    out_dir = env / "exports"
    views = "metric_catalog,company_profile,debt_maturities,top_tenants,lease_expirations,property_metrics"
    assert cli("export", "--out", str(out_dir), "--views", views, "--tickers", "PLD") == 0
    manifest = json.loads(next(out_dir.rglob("manifest.json")).read_text(encoding="utf-8"))
    rows = {k: v["rows"] for k, v in manifest["files"].items()}
    assert rows["debt_maturities"] == 10 and rows["top_tenants"] == 5 and rows["property_metrics"] == 6
    assert rows["metric_catalog"] > 300  # not filtered by ticker
    with open(next(out_dir.rglob("company_profile*.csv")), encoding="utf-8") as fh:
        assert {r["ticker"] for r in csv.DictReader(fh)} == {"PLD"}


def test_debt_ladder_reconciles_to_total_debt(env: Path, sec_root: Path, db: Db) -> None:
    _load(env, sec_root)
    assert cli("dq", "--only", "recon.debt") == 0
    assert _dq(db)["recon.debt_ladder_vs_total_debt"] == "pass"  # no total debt to compare with yet

    # give PLD an XBRL total debt for the same date by cloning its total assets row
    cols = [
        r["COLUMN_NAME"]
        for r in db.query(
            """SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
               AND TABLE_NAME = 'wh_metric_value' AND EXTRA NOT LIKE '%GENERATED%'
               AND COLUMN_NAME NOT IN ('value_id', 'metric_id', 'value', 'row_hash') ORDER BY ORDINAL_POSITION"""
        )
    ]
    src = metric(db, "PLD", "TOTAL_ASSETS", "INSTANT", Q3)[-1]

    def clone(value: str) -> None:
        debt = "(SELECT metric_id FROM ref_metric WHERE metric_code = 'TOTAL_DEBT')"
        db.execute(f"DELETE FROM wh_metric_value WHERE metric_id = {debt}")
        db.execute(
            f"""INSERT INTO wh_metric_value ({", ".join(cols)}, metric_id, value, row_hash)
                SELECT {", ".join(cols)}, {debt}, {value}, SHA2(CONCAT('test-debt', {value}), 256)
                FROM wh_metric_value WHERE value_id = :v""",
            {"v": src["value_id"]},
        )
        db.commit()

    clone("2300000000")  # ladder 2,243,449,000 is within 10%
    assert cli("dq", "--only", "recon.debt") == 0
    assert _dq(db)["recon.debt_ladder_vs_total_debt"] == "pass"
    clone("3000000000")
    assert cli("dq", "--only", "recon.debt") == 0
    assert _dq(db)["recon.debt_ladder_vs_total_debt"] == "fail"
