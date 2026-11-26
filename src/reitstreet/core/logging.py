"""Structured logging. One JSON object per line, with run_id and job attached
automatically, so logs from a scheduled run can be grepped or shipped anywhere."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

run_context: contextvars.ContextVar[dict[str, object]] = contextvars.ContextVar("run_context", default={})  # noqa: B039

_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        doc.update(run_context.get())
        for k, v in record.__dict__.items():
            if k not in _RESERVED and not k.startswith("_"):
                doc[k] = v
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        return json.dumps(doc, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ctx = run_context.get()
        prefix = f"[{ctx.get('job')}#{ctx.get('run_id')}] " if ctx else ""
        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED and not k.startswith("_")}
        tail = (" " + " ".join(f"{k}={v}" for k, v in extras.items())) if extras else ""
        line = f"{datetime.now():%H:%M:%S} {record.levelname:<7} {prefix}{record.getMessage()}{tail}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def setup(level: str = "INFO", fmt: str = "json", log_dir: Path | None = None) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    root.addHandler(console)
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_dir / f"reitstreet_{datetime.now():%Y%m%d}.jsonl", encoding="utf-8")
        fh.setFormatter(JsonFormatter())  # files are always JSON
        root.addHandler(fh)
    for noisy in ("urllib3", "alembic.runtime.migration"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
