"""Fetch profiles: reference/data/ir_profiles.csv (versioned in git) -> ref_ir_profile.

The CSV is the source of truth. It is changed by pull request (regenerate with
scripts/dev/build_ir_profiles.py after a coverage probe, then review the diff), and
synced into the database by `reitstreet universe` and `reitstreet seed`. Nobody edits
ref_ir_profile by hand: the next sync would overwrite it.
"""

from __future__ import annotations

import csv
import hashlib
from datetime import date
from importlib import resources
from typing import Any

from ...core.db import Db

FILE = "ir_profiles.csv"
STRATEGIES = {"static_html", "q4_feed", "headless", "sec_8k", "none"}
PROBE_STATUSES = {"works", "js_only", "no_supp", "error", "not_probed"}


def read_profiles(text: str | None = None) -> list[dict[str, Any]]:
    if text is None:
        text = resources.files("reitstreet.reference").joinpath("data", FILE).read_text(encoding="utf-8")
    rows = []
    for i, r in enumerate(csv.DictReader(text.splitlines()), start=2):
        t = (r.get("ticker") or "").strip()
        if not t:
            continue
        if r["strategy"] not in STRATEGIES:
            raise ValueError(f"{FILE} line {i}: unknown strategy {r['strategy']!r}")
        if r["probe_status"] not in PROBE_STATUSES:
            raise ValueError(f"{FILE} line {i}: unknown probe_status {r['probe_status']!r}")
        rows.append(
            {
                "ticker": t,
                "strategy": r["strategy"],
                "platform": (r.get("platform") or "").strip() or None,
                "expects": 1 if (r.get("expects_supplemental") or "1").strip() == "1" else 0,
                "probe_status": r["probe_status"],
                "probe_links": int(r["probe_supp_links"]) if (r.get("probe_supp_links") or "").strip() else None,
                "probe_period": (r.get("probe_latest_period") or "").strip() or None,
                "probe_date": date.fromisoformat(r["probe_date"]) if (r.get("probe_date") or "").strip() else None,
                "notes": (r.get("notes") or "").strip()[:1024] or None,
            }
        )
    return rows


def apply_profiles(db: Db, text: str | None = None) -> dict[str, int]:
    """Upsert profiles for companies that exist. Returns counts."""
    raw = (
        text if text is not None else resources.files("reitstreet.reference").joinpath("data", FILE).read_text("utf-8")
    )
    label = f"{FILE}@{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12]}"
    rows = read_profiles(raw)
    ids = {
        r["ticker"]: r["company_id"]
        for r in db.query("SELECT company_id, ticker FROM ref_company WHERE ticker IS NOT NULL")
    }
    applied = 0
    for r in rows:
        cid = ids.get(r["ticker"])
        if cid is None:
            continue
        db.execute(
            """INSERT INTO ref_ir_profile (company_id, strategy, platform, expects_supplemental, probe_status,
                 probe_supp_links, probe_latest_period, probe_date, notes, profile_source)
               VALUES (:c, :st, :pl, :ex, :ps, :pn, :pp, :pd, :no, :src) AS new
               ON DUPLICATE KEY UPDATE strategy=new.strategy, platform=new.platform,
                 expects_supplemental=new.expects_supplemental, probe_status=new.probe_status,
                 probe_supp_links=new.probe_supp_links, probe_latest_period=new.probe_latest_period,
                 probe_date=new.probe_date, notes=new.notes, profile_source=new.profile_source""",
            {
                "c": cid,
                "st": r["strategy"],
                "pl": r["platform"],
                "ex": r["expects"],
                "ps": r["probe_status"],
                "pn": r["probe_links"],
                "pp": r["probe_period"],
                "pd": r["probe_date"],
                "no": r["notes"],
                "src": label,
            },
        )
        applied += 1
    db.commit()
    return {"profiles_in_file": len(rows), "profiles_applied": applied}
