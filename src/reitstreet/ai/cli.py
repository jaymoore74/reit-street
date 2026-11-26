"""reitstreet-ai: the local model.  `reitstreet-ai <command> --help` for options.

  doctor         check this machine, the model server and the database
  models         recommended models and whether each fits in this machine's memory
  ping           one short request to the model server
  extract        read the numbers on supplemental pages (a PDF file or a stored document)
  ask            answer a question from the warehouse, with sources and the queries run
  build-dataset  training and test sets from the warehouse (data/ai/datasets/<name>)
  train          LoRA fine-tune with mlx-lm from a recipe (Apple silicon)
  fuse           merge a run's adapter into the base model
  serve          start mlx_lm.server on 127.0.0.1 with a base model, a run, or a fused model
  eval           score a model on the test set and the gold file; compare with a baseline

Exit codes as reitstreet: 0 ok, 1 failed or below the gates, 2 configuration error.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import exc as sa_exc

from .. import __version__
from ..core import config as cfgmod
from ..core import logging as logsetup
from ..core.db import Db
from ..core.errors import ConfigError
from ..core.rawstore import RawStore
from . import ask as askmod
from . import dataset, evaluate, models, train
from . import extract as ex
from .llm import LLMError, client_from_settings

log = logging.getLogger("reitstreet.ai")
EXIT_OK, EXIT_RUN, EXIT_CONFIG = 0, 1, 2


def _db(s: cfgmod.Settings, *, ai: bool = False) -> Db:
    return Db.from_url(s.ai_db_url if (ai and s.ai_db_url) else s.sqlalchemy_url())


def _pages(spec: str | None) -> list[int] | None:
    if not spec:
        return None
    out: list[int] = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            out += list(range(int(a), int(b) + 1))
        elif part.strip():
            out.append(int(part))
    return out


# ----------------------------------------------------------------------------- commands
def cmd_doctor(s: cfgmod.Settings, a: argparse.Namespace) -> int:
    checks: list[tuple[str, bool, str]] = []
    mem = models.machine_memory_gb()
    checks.append(("apple silicon", train.is_apple_silicon(), f"{sys.platform} {os.uname().machine}"))
    checks.append(("memory", mem is not None, f"{mem:.0f} GB" if mem else "unknown"))
    checks.append(("python >= 3.11", sys.version_info >= (3, 11), sys.version.split()[0]))
    try:
        import mlx_lm

        checks.append(("mlx-lm installed", True, getattr(mlx_lm, "__version__", "yes")))
    except ImportError:
        checks.append(("mlx-lm installed", False, "pip install -e .[mac]"))
    checks.append(("config profile", True, s.profile or "(none: ./config.ini)"))
    try:
        c = client_from_settings(s)
        r = c.chat([{"role": "user", "content": "Reply with the word ready."}], max_tokens=8)
        checks.append(("model server", True, f"{c.base_url} {r.model} {r.seconds:.1f}s"))
    except (LLMError, ConfigError) as e:
        checks.append(("model server", False, str(e)[:160]))
    try:
        db = _db(s)
        n = db.scalar("SELECT COUNT(*) FROM ref_company")
        rev = db.scalar("SELECT version_num FROM alembic_version")
        db.close()
        checks.append(("database", True, f"{s.db_host}:{s.db_port}/{s.db_name} rev {rev}, {n} companies"))
    except Exception as e:
        checks.append(("database", False, str(e).splitlines()[0][:160]))
    if s.ai_db_url:
        try:
            db = _db(s, ai=True)
            db.scalar("SELECT COUNT(*) FROM dl_companies")
            db.close()
            checks.append(("read-only login", True, "ok"))
        except Exception as e:
            checks.append(("read-only login", False, str(e).splitlines()[0][:160]))
    for name, ok, detail in checks:
        print(f"{'ok ' if ok else 'NO '} {name:<20} {detail}")
    return EXIT_OK if all(ok for n, ok, _ in checks if n in ("database", "model server")) else EXIT_RUN


def cmd_models(s: cfgmod.Settings, a: argparse.Namespace) -> int:
    if not a.convert:
        print(models.table(models.machine_memory_gb(), s.ai_root))
        return EXIT_OK
    p = models.by_key(a.convert)
    if p is None:
        raise ConfigError(f"unknown model {a.convert!r}: one of {', '.join(m.key for m in models.PRESETS)}")
    cmd = models.convert_command(p, s.ai_root)
    print(shlex.join(cmd))
    if a.dry_run:
        return EXIT_OK
    if not train.is_apple_silicon():
        raise ConfigError("mlx_lm.convert needs Apple silicon; use --dry-run here")
    (s.ai_root / "models").mkdir(parents=True, exist_ok=True)
    return EXIT_OK if subprocess.call(cmd) == 0 else EXIT_RUN  # noqa: S603


def cmd_ping(s: cfgmod.Settings, a: argparse.Namespace) -> int:
    c = client_from_settings(s, model=a.model)
    r = c.chat([{"role": "user", "content": a.text}], max_tokens=64)
    print(json.dumps({"model": r.model, "seconds": round(r.seconds, 2), "reply": r.text}))
    return EXIT_OK


def cmd_extract(s: cfgmod.Settings, a: argparse.Namespace) -> int:
    c = client_from_settings(s, model=a.model)
    ticker, period = a.ticker or "", a.period_end or ""
    if a.pdf:
        data = Path(a.pdf).read_bytes()
    else:
        db = _db(s)
        d = db.one(
            """SELECT d.sha256, d.period_end_date, c.ticker FROM stg_document d
               LEFT JOIN ref_company c ON c.company_id = d.company_id WHERE d.doc_id = :d""",
            {"d": a.doc_id},
        )
        db.close()
        if d is None or not d["sha256"]:
            print(f"document {a.doc_id} not found or not fetched", file=sys.stderr)
            return EXIT_CONFIG
        data = RawStore.local(s.raw_root).get(d["sha256"])
        ticker = ticker or (d["ticker"] or "")
        period = period or (d["period_end_date"].isoformat() if d["period_end_date"] else "")
    rules = ex.load_label_rules()
    results = []
    for page_no, text in ex.pdf_pages(data, _pages(a.pages)).items():
        if len(text.strip()) < 40:
            continue
        results.append(ex.extract_page(c, text, ticker=ticker, period_end=period, page_no=page_no, rules=rules))
    out = ex.dumps(results)
    if a.out:
        Path(a.out).write_text(out, encoding="utf-8")
        n = sum(len(r.values) for r in results)
        print(
            json.dumps(
                {
                    "pages": len(results),
                    "values": n,
                    "ungrounded_dropped": sum(r.ungrounded for r in results),
                    "out": a.out,
                }
            )
        )
    else:
        print(out)
    return EXIT_RUN if any(r.error for r in results) else EXIT_OK


def cmd_ask(s: cfgmod.Settings, a: argparse.Namespace) -> int:
    db = _db(s, ai=True)
    try:
        res = askmod.ask(db, client_from_settings(s, model=a.model), a.question, max_steps=a.max_steps)
    finally:
        db.close()
    if a.json:
        print(json.dumps(res.as_dict(), indent=2))
    else:
        print(res.answer)
        if res.sources:
            print("\nSources:\n" + "\n".join(f"- {x}" for x in res.sources))
        if res.queries:
            print("\nQueries run:\n" + "\n".join(f"- {q}" for q in res.queries))
    return EXIT_OK if res.answer else EXIT_RUN


def cmd_build_dataset(s: cfgmod.Settings, a: argparse.Namespace) -> int:
    out = Path(a.out) if a.out else s.ai_root / "datasets" / a.name
    db = _db(s)
    try:
        m = dataset.build(
            db,
            RawStore.local(s.raw_root),
            out,
            min_confidence=a.min_confidence,
            max_values=a.max_values,
            qa_per_company=a.qa_per_company,
            tickers=[t.strip().upper() for t in a.tickers.split(",")] if a.tickers else None,
            gold=Path(a.gold) if a.gold else None,
            skills=tuple(a.skills.split(",")),
        )
    finally:
        db.close()
    print(
        json.dumps(
            {
                "out": str(out),
                "stats": m["stats"],
                "files": {k: v["lines"] for k, v in m["files"].items()},
                "warning": m.get("warning"),
            },
            indent=2,
        )
    )
    return EXIT_OK


def cmd_train(s: cfgmod.Settings, a: argparse.Namespace) -> int:
    if a.list:
        print("\n".join(train.list_recipes()))
        return EXIT_OK
    run = train.prepare(a.recipe, s.ai_root, Path(a.data) if a.data else None)
    print(f"run folder: {run.path}\ncommand: {shlex.join(run.command)}")
    if a.dry_run:
        return EXIT_OK
    return EXIT_OK if train.execute(run) == 0 else EXIT_RUN


def cmd_fuse(s: cfgmod.Settings, a: argparse.Namespace) -> int:
    cmd = train.fuse_command(Path(a.run), out=Path(a.out) if a.out else None)
    print(shlex.join(cmd))
    if a.dry_run:
        return EXIT_OK
    if not train.is_apple_silicon():
        raise ConfigError("fusing with mlx-lm needs Apple silicon; use --dry-run here")
    return subprocess.call(cmd)  # noqa: S603


def cmd_serve(s: cfgmod.Settings, a: argparse.Namespace) -> int:
    adapter = None
    model = a.model or s.llm_model
    if a.run:
        run = Path(a.run)
        meta = json.loads((run / "run.json").read_text(encoding="utf-8"))
        if (run / "fused").exists() and not a.adapter:
            model = str((run / "fused").resolve())
        else:
            model, adapter = meta["base_model"], run / "adapter"
    if not model:
        raise ConfigError("give --model, --run, or set [llm] model")
    model = models.resolve(model, s.ai_root, must_exist=not a.dry_run and train.is_apple_silicon())
    cmd = train.serve_command(model, adapter=adapter, port=a.port)
    print(shlex.join(cmd))
    if a.dry_run:
        return EXIT_OK
    if not train.is_apple_silicon():
        raise ConfigError("mlx_lm.server needs Apple silicon; on other machines serve with Ollama or llama.cpp")
    return subprocess.call(cmd)  # noqa: S603


def cmd_eval(s: cfgmod.Settings, a: argparse.Namespace) -> int:
    data = Path(a.data) if a.data else s.ai_root / "datasets" / a.name
    client = client_from_settings(s, model=a.model)
    rep: dict[str, Any] = {"model": a.label or a.model or s.llm_model or "server default", "data": str(data)}
    if a.kind in ("extract", "all"):
        cases = evaluate.read_cases(data / "eval_extract.jsonl")
        if cases:
            sc = evaluate.eval_extract(client, cases, limit=a.limit)
            rep["extract"] = sc.summary()
            rep["extract_worst_pages"] = sc.worst
    if a.kind in ("qa", "all"):
        cases = evaluate.read_cases(data / "eval_qa.jsonl")
        if cases:
            db = _db(s, ai=True)
            try:
                qs = evaluate.eval_qa(db, client, cases, limit=a.limit)
            finally:
                db.close()
            rep["qa"] = qs.summary()
            rep["qa_misses"] = qs.misses
    rep["gates"] = evaluate.gates(rep)
    out = Path(a.out) if a.out else data / "reports" / f"{(a.label or 'eval').replace('/', '_')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(evaluate.to_markdown(rep), encoding="utf-8")
    print(evaluate.to_markdown(rep))
    if a.baseline:
        before = json.loads(Path(a.baseline).read_text(encoding="utf-8"))
        print("Change against baseline:\n" + "\n".join(evaluate.compare(rep, before)))
    return EXIT_OK if all(rep["gates"].values()) else EXIT_RUN


# ----------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="reitstreet-ai", description="Local model for REIT Street.")
    p.add_argument("--version", action="version", version=f"reitstreet-ai {__version__}")
    p.add_argument(
        "--config", help="config path (default: REITSTREET_CONFIG, config.<REITSTREET_PROFILE>.ini, ./config.ini)"
    )
    p.add_argument("-v", "--verbose", action="store_true")
    sp = p.add_subparsers(dest="cmd", required=True, metavar="command")

    def cmd(name: str, fn: Any, help_: str) -> argparse.ArgumentParser:
        sub = sp.add_parser(name, help=help_, description=help_)
        sub.set_defaults(func=fn)
        return sub

    def model(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--model", help="model id on the server (default: [llm] model)")

    cmd("doctor", cmd_doctor, "check this machine, the model server and the database")
    mo = cmd("models", cmd_models, "recommended models and whether each fits this machine")
    mo.add_argument("--convert", metavar="KEY", help="download and convert a model to data/ai/models/KEY")
    mo.add_argument("--dry-run", action="store_true")
    pg = cmd("ping", cmd_ping, "one short request to the model server")
    pg.add_argument("text", nargs="?", default="In one sentence: what is AFFO?")
    model(pg)
    e = cmd("extract", cmd_extract, "read the numbers on supplemental pages")
    src = e.add_mutually_exclusive_group(required=True)
    src.add_argument("--pdf", help="a PDF file")
    src.add_argument("--doc-id", type=int, help="a stored document (stg_document.doc_id)")
    e.add_argument("--pages", help="pages, e.g. 5,7-9 (default: all)")
    e.add_argument("--ticker")
    e.add_argument("--period-end", help="YYYY-MM-DD")
    e.add_argument("--out", help="write JSON here instead of printing it")
    model(e)
    q = cmd("ask", cmd_ask, "answer a question from the warehouse")
    q.add_argument("question")
    q.add_argument("--max-steps", type=int, default=askmod.MAX_STEPS)
    q.add_argument("--json", action="store_true", help="answer, sources and queries as JSON")
    model(q)
    b = cmd("build-dataset", cmd_build_dataset, "training and test sets from the warehouse")
    b.add_argument("--name", default="current", help="folder under data/ai/datasets (default: current)")
    b.add_argument("--out", help="folder (overrides --name)")
    b.add_argument("--skills", default="extract,qa", help="extract, qa or both")
    b.add_argument("--min-confidence", type=float, default=0.9)
    b.add_argument("--max-values", type=int, default=120, help="leave out pages with more values than this")
    b.add_argument("--qa-per-company", type=int, default=40)
    b.add_argument("--tickers", help="comma-separated (default: all)")
    b.add_argument("--gold", help="hand-checked values (JSON lines), always test")
    t = cmd("train", cmd_train, "LoRA fine-tune with mlx-lm from a recipe")
    t.add_argument("--recipe", default="extract-qa")
    t.add_argument("--data", help="dataset folder (default: the recipe's dataset under data/ai/datasets)")
    t.add_argument("--dry-run", action="store_true", help="write the run folder and print the command only")
    t.add_argument("--list", action="store_true", help="list built-in recipes")
    f = cmd("fuse", cmd_fuse, "merge a run's adapter into the base model")
    f.add_argument("--run", required=True, help="run folder under data/ai/runs")
    f.add_argument("--out", help="output folder (default: <run>/fused)")
    f.add_argument("--dry-run", action="store_true")
    sv = cmd("serve", cmd_serve, "start mlx_lm.server on 127.0.0.1")
    sv.add_argument("--run", help="a training run: serves <run>/fused, or the base model with the adapter")
    sv.add_argument("--adapter", action="store_true", help="with --run: serve base + adapter even if fused exists")
    sv.add_argument("--port", type=int, default=8080)
    sv.add_argument("--dry-run", action="store_true")
    model(sv)
    ev = cmd("eval", cmd_eval, "score a model on the test set and the gold file")
    ev.add_argument("--name", default="current")
    ev.add_argument("--data", help="dataset folder (overrides --name)")
    ev.add_argument("--kind", choices=["extract", "qa", "all"], default="all")
    ev.add_argument("--limit", type=int, help="first N cases of each kind")
    ev.add_argument("--label", help="name for the report, e.g. base or run-20270301")
    ev.add_argument("--out", help="report path (default: <data>/reports/<label>.json)")
    ev.add_argument("--baseline", help="an earlier report.json to compare with")
    model(ev)
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        s = cfgmod.load(a.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return EXIT_CONFIG
    logsetup.setup("DEBUG" if a.verbose else s.logging_level, "text", s.data_dir / "logs")
    try:
        return int(a.func(s, a))
    except ConfigError as e:
        print(f"configuration error: {e}", file=sys.stderr)
        return EXIT_CONFIG
    except LLMError as e:
        print(f"model error: {e}", file=sys.stderr)
        return EXIT_RUN
    except sa_exc.OperationalError as e:
        print(f"error: cannot reach the database: {str(e.orig).splitlines()[0]}", file=sys.stderr)
        return EXIT_RUN


if __name__ == "__main__":
    raise SystemExit(main())
