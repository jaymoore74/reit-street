"""Local model layer against the warehouse (MySQL), with a scripted model."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from reitstreet.ai import ask as askmod
from reitstreet.ai import dataset, evaluate
from reitstreet.ai.llm import ScriptedClient
from reitstreet.core.db import Db
from reitstreet.core.http import store_local_file
from reitstreet.core.rawstore import RawStore
from tests.ai.pdfmini import SUPPLEMENTAL_PAGE, make_pdf
from tests.integration.helpers import bootstrap

pytestmark = pytest.mark.mysql


def _a_metric(db: Db) -> dict[str, Any]:
    r = db.one(
        """SELECT ticker, metric_code, period_type, period_end, value, unit FROM dl_metrics
           WHERE is_preferred = 1 AND segment = 'Total' AND period_type = 'FY' ORDER BY ticker, metric_code LIMIT 1"""
    )
    assert r is not None
    return dict(r)


def test_ask_runs_read_only_sql_and_returns_the_answer_with_sources(env: Path, sec_root: Path, db: Db) -> None:
    bootstrap(sec_root)
    m = _a_metric(db)
    sql = (
        "SELECT value, unit, period_end FROM dl_metrics "
        f"WHERE ticker='{m['ticker']}' AND metric_code='{m['metric_code']}' AND period_end='{m['period_end']}'"
    )
    seen: list[str] = []

    def model(msgs: Sequence[dict[str, Any]]) -> str:
        last = msgs[-1]["content"]
        seen.append(last)
        if len(seen) == 1:
            return json.dumps({"action": "sql", "sql": "DELETE FROM dl_metrics"})
        if len(seen) == 2:
            assert last.startswith("ERROR query refused")
            return json.dumps({"action": "sql", "sql": "SELECT * FROM wh_metric_value"})
        if len(seen) == 3:
            assert "not allowed" in last
            return "Sure! " + json.dumps({"action": "sql", "sql": sql})
        assert last.startswith("RESULT") and str(m["period_end"]) in last
        return json.dumps({"action": "answer", "answer": f"Value {m['value']}", "sources": ["xbrl"]})

    a = askmod.ask(db, ScriptedClient(model), "What was it?")
    assert a.answer.startswith("Value") and a.queries == [sql] and len(a.errors) == 2
    # the session is back to normal (READ ONLY ended) and nothing was deleted
    assert db.scalar("SELECT COUNT(*) FROM wh_metric_value") > 0
    db.execute("CREATE TEMPORARY TABLE tmp_rw_check (x INT)")  # fails if READ ONLY leaked
    db.execute("INSERT INTO tmp_rw_check VALUES (1)")
    db.rollback()


def test_ask_preloads_the_profile_of_a_named_reit(env: Path, sec_root: Path, db: Db) -> None:
    bootstrap(sec_root)
    t = _a_metric(db)["ticker"]
    c = ScriptedClient([json.dumps({"action": "answer", "answer": "ok", "sources": []})])
    a = askmod.ask(db, c, f"Give me everything on {t}")
    assert a.answer == "ok" and f"profile:{t}" in a.sources
    sent = c.calls[0]
    assert any(msg["content"].startswith("RESULT") and t in msg["content"] for msg in sent)
    assert "dl_metrics(" in sent[0]["content"]  # live schema in the system prompt


def _seed_parsed_supplemental(db: Db, root: Path) -> str:
    store = RawStore.local(root / "data" / "raw")
    pdf = root / "ACME_2024Q3_supplemental.pdf"
    pdf.write_bytes(make_pdf([SUPPLEMENTAL_PAGE]))
    sha, _ = store_local_file(db, store, pdf, source="manual_archive", run_id=None, content_type="application/pdf")
    c = db.one("SELECT company_id, ticker FROM ref_company ORDER BY ticker LIMIT 1")
    assert c is not None
    doc = db.execute(
        """INSERT INTO stg_document (company_id, doc_kind, source, source_url, sha256, period_end_date, status)
           VALUES (:c, 'supplemental', 'manual_archive', :u, :s, '2024-09-30', 'parsed')""",
        {"c": c["company_id"], "u": pdf.as_uri(), "s": sha},
    )
    parse = db.execute(
        "INSERT INTO stg_doc_parse (doc_id, sha256, parser_name, parser_version, status) "
        "VALUES (:d, :s, 'test', '1', 'succeeded')",
        {"d": doc, "s": sha},
    )
    table = db.execute(
        "INSERT INTO stg_doc_table (parse_id, doc_id, page_no, table_index, title) "
        "VALUES (:p, :d, 1, 1, 'Same-Property NOI')",
        {"p": parse, "d": doc},
    )
    rows = [
        ("Rental revenues", "Q3 2024", "125,400", 125400000, "$", 0.99),
        ("Property operating expenses", "Q3 2024", "(41,200)", -41200000, "$", 0.99),
        ("Same-property NOI", "Q3 2024", "84,200", 84200000, "$", 0.99),
        ("Occupancy", "Q3 2024", "96.1%", 96.1, "%", 0.97),
        ("NOI growth", "Q3 2024", "5.2%", 5.2, "%", 0.5),  # low confidence: left out
        ("Phantom row", "Q3 2024", "77,777", 77777000, "$", 0.99),  # not on the page: left out
    ]
    for i, (label, col, raw, val, unit, conf) in enumerate(rows, start=1):
        db.execute(
            """INSERT INTO stg_doc_line_item (parse_id, table_id, doc_id, page_no, row_index, col_index, row_label,
                 col_label, raw_text, value_numeric, scale_hint, unit_hint, period_end, confidence)
               VALUES (:p, :t, :d, 1, :i, 1, :l, :c, :r, :v, 'in thousands', :u, '2024-09-30', :conf)""",
            {
                "p": parse,
                "t": table,
                "d": doc,
                "i": i,
                "l": label,
                "c": col,
                "r": raw,
                "v": val,
                "u": unit,
                "conf": conf,
            },
        )
    db.execute("UPDATE stg_document SET current_parse_id = :p WHERE doc_id = :d", {"p": parse, "d": doc})
    db.commit()
    return str(c["ticker"])


def test_build_dataset_writes_mlx_chat_files_split_by_company(env: Path, sec_root: Path, db: Db) -> None:
    bootstrap(sec_root)
    ticker = _seed_parsed_supplemental(db, env)
    gold = env / "gold.jsonl"
    gold.write_text(
        "# hand-checked\n"
        '{"ticker": "acme", "metric_code": "TOTAL_REVENUE", "period_end": "2024-09-30", '
        '"value": 125400000, "unit": "USD"}\n',
        encoding="utf-8",
    )
    out = env / "data" / "ai" / "datasets" / "t1"
    m = dataset.build(db, RawStore.local(env / "data" / "raw"), out, qa_per_company=5, gold=gold)
    st = m["stats"]
    assert st["pages_kept"] == 1 and st["values_kept"] == 4
    assert st["values_dropped_low_conf"] == 1 and st["values_dropped_ungrounded"] == 1
    assert st["qa_examples"] > 0 and st["qa_missing_examples"] > 0 and st["gold_examples"] == 1
    for f in ("train.jsonl", "valid.jsonl", "test.jsonl", "eval_extract.jsonl", "eval_qa.jsonl", "manifest.json"):
        assert (out / f).exists(), f
    lines = [json.loads(x) for f in ("train", "valid", "test") for x in (out / f"{f}.jsonl").read_text().splitlines()]
    assert all(set(r) == {"messages"} for r in lines)
    ext = [r for r in lines if r["messages"][0]["content"].startswith("You read pages")]
    assert len(ext) == 1
    target = json.loads(ext[0]["messages"][-1]["content"])
    assert target["scale"] == "thousands"
    assert {v["label"]: v["unit"] for v in target["values"]} == {
        "Rental revenues": "USD",
        "Property operating expenses": "USD",
        "Same-property NOI": "USD",
        "Occupancy": "pct",
    }
    # every ticker lands in exactly one split
    splits: dict[str, set[str]] = {}
    for sp in ("train", "valid", "test"):
        for x in (out / f"{sp}.jsonl").read_text().splitlines():
            msgs = json.loads(x)["messages"]
            if msgs[0]["content"].startswith("You read pages"):
                splits.setdefault(ticker, set()).add(sp)
    assert all(len(v) == 1 for v in splits.values())
    # the QA targets are true: re-running a training query gives the row in the conversation
    qa = next(
        r
        for r in lines
        if r["messages"][0]["content"].startswith("You are a REIT research analyst")
        and json.loads(r["messages"][3]["content"].removeprefix("RESULT "))
    )
    sql = json.loads(qa["messages"][2]["content"])["sql"]
    assert json.loads(qa["messages"][3]["content"].removeprefix("RESULT ")) == json.loads(
        askmod.rows_json(askmod.run_readonly(db, sql))
    )


def test_eval_qa_scores_a_scripted_model(env: Path, sec_root: Path, db: Db) -> None:
    bootstrap(sec_root)
    m = _a_metric(db)
    cases = [
        {"kind": "qa", "ticker": m["ticker"], "question": "q1", "expected_value": str(m["value"])},
        {"kind": "qa", "ticker": m["ticker"], "question": "q2", "expected_value": None},
        {"kind": "qa", "ticker": m["ticker"], "question": "q3", "expected_value": "1", "gold": True},
    ]
    replies = [
        json.dumps({"action": "answer", "answer": f"It was {m['value']:,}."}),
        json.dumps({"action": "answer", "answer": "That metric is not available in the warehouse."}),
        json.dumps({"action": "answer", "answer": "About 2."}),
    ]
    sc = evaluate.eval_qa(db, ScriptedClient(replies), cases)
    s = sc.summary()
    assert s["accuracy"] == 1.0 and s["gold_accuracy"] == 0.0 and len(sc.misses) == 1
