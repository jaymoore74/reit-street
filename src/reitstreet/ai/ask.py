"""Answer questions from the warehouse: `reitstreet-ai ask "give me everything on NNN"`.

The model never sees the database directly. It works in a loop of small JSON moves:

  {"action": "profile", "ticker": "NNN"}             everything relevant for one REIT
  {"action": "sql", "sql": "SELECT ... FROM dl_..."}  a read-only query on the client views
  {"action": "answer", "answer": "...", "sources": ["..."]}

Each query passes sqlguard.check(), then runs in a READ ONLY transaction with a time
limit, ideally under a read-only login ([ai] db_url). Results go back to the model as
JSON. The answer must cite where its numbers came from; every query run is returned with
the answer so a person can re-run it.

The JSON protocol (rather than a backend's native tool calling) is deliberate: it works
the same on mlx_lm.server, Ollama and llama.cpp, and it is what the fine-tuning data
teaches (dataset.py), so training and use match.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ..core.db import Db
from ..delivery import profile as prof
from . import sqlguard
from .llm import ChatClient, LLMError, parse_json

MAX_STEPS = 6
MAX_ROWS = 200
STATEMENT_MS = 15000
RESULT_CHARS = 12000  # per tool result fed back to the model

SYSTEM = """You are a REIT research analyst with read-only access to a REIT data warehouse.
Answer only from data you retrieve. Never guess a number. If the data is not there, say so.

Reply with exactly one JSON object per turn, nothing else:
  {{"action": "profile", "ticker": "TICKER"}}  everything relevant for one REIT
  {{"action": "sql", "sql": "SELECT ..."}}      one read-only query on the views below
  {{"action": "answer", "answer": "...", "sources": ["..."]}}

Rules for SQL: one SELECT, MySQL 8 syntax, only these views and tables, filter by ticker,
prefer is_preferred = 1 in dl_metrics, and order by period_end DESC for latest values.
Values are in the unit given (USD, USD/shares, pct, sf, units). Quote periods with numbers.
In the answer, state each number with its period and its source (source_type, document
or filing, and page when known). Be concise and numbers-first.

Views and columns:
{schema}"""


def _plain(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v) if v != v.to_integral() else int(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return v


def rows_json(rows: list[dict[str, Any]], limit: int = RESULT_CHARS) -> str:
    out = json.dumps([{k: _plain(v) for k, v in r.items()} for r in rows], default=str)
    if len(out) > limit:
        out = out[:limit] + f" ... [truncated: {len(rows)} rows, narrow the query]"
    return out


def schema_text(db: Db) -> str:
    """Columns of the views the model may query, read from the live schema."""
    rows = db.query(
        """SELECT TABLE_NAME AS t, GROUP_CONCAT(COLUMN_NAME ORDER BY ORDINAL_POSITION SEPARATOR ', ') AS cols
           FROM INFORMATION_SCHEMA.COLUMNS
           WHERE TABLE_SCHEMA = DATABASE() AND (TABLE_NAME LIKE 'dl\\_%' OR TABLE_NAME IN ('ref_company','ref_metric'))
           GROUP BY TABLE_NAME ORDER BY TABLE_NAME"""
    )
    return "\n".join(f"- {r['t']}({r['cols']})" for r in rows)


def run_readonly(db: Db, sql: str, *, max_rows: int = MAX_ROWS) -> list[dict[str, Any]]:
    """Run a model-written query under every read-only guard we have."""
    safe = sqlguard.check(sql, max_rows=max_rows)
    db.rollback()  # end any open transaction so READ ONLY applies to this one
    db.conn.exec_driver_sql(f"SET SESSION max_execution_time = {STATEMENT_MS}")
    db.conn.exec_driver_sql("START TRANSACTION READ ONLY")
    try:
        return db.query(safe)
    finally:
        db.rollback()


def company_profile(db: Db, ticker: str) -> dict[str, Any] | None:
    """The client profile (delivery.profile), keeping only metrics that have a value so the
    context stays small."""
    t = ticker.strip().upper()
    if not re.fullmatch(r"[A-Z.\-]{1,10}", t):
        return None
    db.rollback()
    p = prof.company_profile(db, t)
    if p is not None:
        p["metrics"] = [m for m in p["metrics"] if m.get("has_value")]
    return p


@dataclass
class Answer:
    answer: str
    sources: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    steps: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "sources": self.sources,
            "queries": self.queries,
            "steps": self.steps,
            "errors": self.errors,
        }


TICKER_HINT = re.compile(r"\b[A-Z]{1,5}\b")


def known_tickers(db: Db, question: str) -> list[str]:
    cands = sorted(set(TICKER_HINT.findall(question)))
    if not cands:
        return []
    found = {r["ticker"] for r in db.query("SELECT ticker FROM ref_company")}
    return [c for c in cands if c in found][:3]


def ask(db: Db, client: ChatClient, question: str, *, max_steps: int = MAX_STEPS) -> Answer:
    msgs: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM.format(schema=schema_text(db))},
        {"role": "user", "content": question},
    ]
    result = Answer(answer="")
    # Save a step: preload the profile of any REIT named in the question.
    for t in known_tickers(db, question):
        p = company_profile(db, t)
        if p is not None:
            msgs.append({"role": "assistant", "content": json.dumps({"action": "profile", "ticker": t})})
            msgs.append({"role": "user", "content": "RESULT " + rows_json([p])})
            result.sources.append(f"profile:{t}")
    for step in range(1, max_steps + 1):
        result.steps = step
        reply = client.chat(msgs)
        msgs.append({"role": "assistant", "content": reply.text})
        try:
            move = parse_json(reply.text)
            if not isinstance(move, dict):
                raise LLMError("reply must be a JSON object")
            action = str(move.get("action", "")).lower()
        except LLMError as e:
            result.errors.append(str(e))
            msgs.append({"role": "user", "content": "ERROR reply with one JSON object as instructed"})
            continue
        if action == "answer":
            result.answer = str(move.get("answer", "")).strip()
            result.sources += [str(s) for s in move.get("sources") or []]
            return result
        if action == "profile":
            p = company_profile(db, str(move.get("ticker", "")))
            content = "RESULT " + rows_json([p]) if p else "ERROR unknown ticker"
        elif action == "sql":
            sql = str(move.get("sql", ""))
            try:
                rows = run_readonly(db, sql)
                result.queries.append(sql)
                content = "RESULT " + rows_json(rows)
            except sqlguard.UnsafeSql as e:
                content = f"ERROR query refused: {e}"
                result.errors.append(content)
            except Exception as e:  # SQL error: tell the model so it can fix the query
                db.rollback()
                content = f"ERROR {str(e).splitlines()[0][:300]}"
                result.errors.append(content)
        else:
            content = "ERROR unknown action; use profile, sql or answer"
        msgs.append({"role": "user", "content": content})
    msgs.append({"role": "user", "content": "Step limit reached. Answer now from what you have."})
    reply = client.chat(msgs)
    try:
        move = parse_json(reply.text)
        result.answer = str(move.get("answer", "")) if isinstance(move, dict) else reply.text
    except LLMError:
        result.answer = reply.text.strip()
    return result
