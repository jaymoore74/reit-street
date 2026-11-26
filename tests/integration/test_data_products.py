"""Data quality, exports and supplementals (archive import, manual parser, IR fetch)."""

from __future__ import annotations

import csv
import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from reitstreet.core.db import Db
from tests.integration.helpers import bootstrap, cli, last_run, metric
from tests.sec_fixtures import IR_HOST, quarters_2024

pytestmark = pytest.mark.mysql


def _dq(db: Db) -> dict[str, str]:
    run = last_run(db, "dq")["run_id"]
    return {
        r["check_code"]: r["status"]
        for r in db.query(
            "SELECT check_code, status FROM ops_dq_result WHERE run_id=:r AND subject='summary'", {"r": run}
        )
    }


def test_dq_passes_on_clean_data_and_catches_breaks(env: Path, sec_root: Path, db: Db) -> None:
    bootstrap(sec_root)
    assert cli("dq") == 0
    res = _dq(db)
    expected_pass = (
        "contract.lineage",
        "contract.single_current",
        "contract.knowledge_chain",
        "contract.raw_objects",
        "accounting.balance_sheet",
        "accounting.quarters_sum_to_fy",
        "anomaly.sign",
        "recon.supplemental_vs_xbrl",
        "ops.dead_letters",
    )
    for code in expected_pass:
        assert res[code] == "pass", (code, res)
    assert "error" not in res.values()
    # break the knowledge chain and lineage on purpose
    db.execute("UPDATE wh_metric_value SET known_to=NULL WHERE known_to IS NOT NULL LIMIT 1")
    db.execute("UPDATE wh_metric_value SET source_fact_id=NULL WHERE source_type='xbrl' LIMIT 1")
    db.commit()
    assert cli("dq", "--only", "contract") == 0
    res = _dq(db)
    assert res["contract.knowledge_chain"] == "fail"
    assert res["contract.single_current"] == "fail"
    assert res["contract.lineage"] == "fail"
    assert db.scalar("SELECT COUNT(*) FROM ops_alert WHERE source='dq'") == 1


def test_dq_detects_corrupt_raw_object(env: Path, sec_root: Path, db: Db) -> None:
    bootstrap(sec_root)
    rel = db.scalar("SELECT storage_path FROM raw_object ORDER BY first_seen_at DESC LIMIT 1")
    (env / "data" / "raw" / rel).write_bytes(b"garbage")
    assert cli("dq", "--only", "contract.raw") == 0
    assert _dq(db)["contract.raw_objects"] == "fail"


def _read(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_export_current_and_point_in_time(env: Path, sec_root: Path, db: Db) -> None:
    bootstrap(sec_root)
    out = env / "exports"
    q1 = quarters_2024("PLD")[0]

    def pld_q1(rows: list[dict[str, str]]) -> list[str]:
        return [
            r["value"]
            for r in rows
            if r["ticker"] == "PLD" and r["metric_code"] == "TOTAL_REVENUE" and r["period_type"] == "Q"
            and r["period_end"] == "2024-03-31" and r["source_type"] == "xbrl"
        ]  # fmt: skip

    assert cli("export", "--out", str(out), "--views", "companies,metrics,metrics_history") == 0
    d1 = sorted(out.iterdir())[-1]
    manifest = json.loads((d1 / "manifest.json").read_text())
    assert manifest["files"]["companies"]["rows"] == 12
    assert manifest["alembic_revision"] == "0003"
    assert pld_q1(_read(d1 / "metrics.csv")) == [str(int(q1 * 1.01))]
    assert sorted(pld_q1(_read(d1 / "metrics_history.csv"))) == sorted([str(q1), str(int(q1 * 1.01))])

    # as known on 2024-12-31: before the 2025 restatement, no FY 2024 yet
    (out / "pit").mkdir()
    assert cli("export", "--out", str(out / "pit"), "--views", "metrics", "--as-of-date", "2024-12-31") == 0
    pit = _read(next((out / "pit").iterdir()) / "metrics.csv")
    assert pld_q1(pit) == [str(q1)]
    assert not [r for r in pit if r["period_end"] == "2024-12-31" and r["period_type"] == "FY"]

    # as recorded before a mapping change, after which the rows were retired
    stamp = db.scalar("SELECT DATE_FORMAT(UTC_TIMESTAMP(6), '%Y-%m-%dT%H:%i:%s.%f')")
    db.execute("UPDATE ref_metric_map SET is_active=0 WHERE map_key='xbrl:TOTAL_REVENUE:us-gaap:Revenues:USD'")
    db.commit()
    assert cli("build-metrics", "--tickers", "PLD") == 0
    (out / "rec").mkdir()
    assert cli("export", "--out", str(out / "rec"), "--views", "metrics", "--as-recorded", stamp) == 0
    assert pld_q1(_read(next((out / "rec").iterdir()) / "metrics.csv")) == [str(int(q1 * 1.01))]


def test_archive_import_and_manual_parse_feed_supplemental_metrics(env: Path, sec_root: Path, db: Db) -> None:
    bootstrap(sec_root)
    arch = env / "archive"
    (arch / "PLD").mkdir(parents=True)
    q3 = quarters_2024("PLD")[2]
    (arch / "PLD" / "PLD_2024Q3_supplemental_keyed.csv").write_text(
        "page_no,row_label,value,col_label,period_start,period_end,segment,unit_hint\n"
        f"7,Total revenues,{q3},Three months ended,2024-07-01,2024-09-30,,\n"
        '7,FFO attributable to common stockholders,"1,234,567",Q3 2024,2024-07-01,2024-09-30,,\n'
        "7,FFO per diluted share,1.45,Q3 2024,2024-07-01,2024-09-30,,\n"
        "9,Period end occupancy,96.8%,,,2024-09-30,,\n",
        encoding="utf-8",
    )
    (arch / "mystery-file.pdf").write_bytes(b"%PDF-1.4 unknown")
    (arch / "PLD" / "copy-of-q3.csv").write_bytes((arch / "PLD" / "PLD_2024Q3_supplemental_keyed.csv").read_bytes())
    assert cli("import-archive", str(arch)) == 0
    assert db.scalar("SELECT COUNT(*) FROM stg_document WHERE source='manual_archive'") == 3
    assert db.scalar("SELECT COUNT(*) FROM stg_document WHERE status='duplicate'") == 1
    assert db.scalar("SELECT COUNT(*) FROM stg_document WHERE status='needs_review'") == 1
    assert cli("import-archive", str(arch)) == 0  # second import: nothing new
    assert db.scalar("SELECT COUNT(*) FROM stg_document WHERE source='manual_archive'") == 3

    assert cli("parse-docs") == 0
    ffo = metric(db, "PLD", "FFO_NAREIT", "Q", "2024-09-30", source="supplemental")
    assert [r["value"] for r in ffo] == [Decimal("1234567")]
    assert ffo[0]["source_page_no"] == 7 and ffo[0]["source_doc_id"] and ffo[0]["source_item_id"]
    assert [r["value"] for r in metric(db, "PLD", "FFO_PER_SHARE", "Q", "2024-09-30", "supplemental")] == [
        Decimal("1.45")
    ]
    occ = metric(db, "PLD", "OCCUPANCY", "INSTANT", "2024-09-30", source="supplemental")
    assert len(occ) == 1
    # supplemental revenue agrees with XBRL, so the reconciliation passes; then make it disagree
    rev = metric(db, "PLD", "TOTAL_REVENUE", "Q", "2024-09-30", source="supplemental")
    assert [r["value"] for r in rev] == [Decimal(q3)]
    assert cli("dq", "--only", "recon") == 0
    assert _dq(db)["recon.supplemental_vs_xbrl"] == "pass"
    db.execute("UPDATE stg_doc_line_item SET value_numeric=value_numeric*1.1 WHERE row_label='Total revenues'")
    db.commit()
    assert cli("build-metrics", "--tickers", "PLD") == 0
    assert cli("dq", "--only", "recon") == 0
    assert _dq(db)["recon.supplemental_vs_xbrl"] == "fail"


def test_fetch_ir_keeps_each_version_of_a_reused_url(env: Path, sec_root: Path, db: Db, tmp_path: Path) -> None:
    site = tmp_path / "site"
    shutil.copytree(sec_root, site)
    assert cli("migrate") == 0
    assert cli("universe") == 0
    assert cli("fetch-ir", "--offline-fixtures", str(site)) == 0
    assert db.scalar("SELECT COUNT(*) FROM stg_document WHERE source='ir_website'") == 2  # PLD and O
    assert cli("fetch-ir", "--offline-fixtures", str(site)) == 0
    assert db.scalar("SELECT COUNT(*) FROM stg_document WHERE source='ir_website'") == 2  # unchanged
    (site / IR_HOST / "pld" / "latest-supplemental.pdf").write_bytes(b"%PDF-1.4 next quarter")
    assert cli("fetch-ir", "--offline-fixtures", str(site), "--tickers", "PLD") == 0
    assert db.scalar("SELECT COUNT(*) FROM stg_document WHERE source='ir_website'") == 3
    assert db.scalar("SELECT COUNT(*) FROM ref_ir_source WHERE source_type='supplemental_pdf' "
                     "AND last_sha256 IS NOT NULL") == 2  # fmt: skip
