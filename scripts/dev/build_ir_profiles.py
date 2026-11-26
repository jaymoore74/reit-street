"""Build src/reitstreet/reference/data/ir_profiles.csv from the universe file and a
coverage probe (CSV rows: ticker,status,supp_links,latest_supp_url,latest_period,platform,note).

Run when the universe changes or after a new probe; review the diff in a PR like code.
    python scripts/dev/build_ir_profiles.py ../universe/reits.csv probe.csv --date 2026-11-26
Rows already in the output keep any hand edits to strategy and expects_supplemental
unless --reset is given.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "src" / "reitstreet" / "reference" / "data" / "ir_profiles.csv"
FIELDS = [
    "ticker", "strategy", "platform", "expects_supplemental", "probe_status", "probe_supp_links",
    "probe_latest_period", "probe_date", "notes",
]  # fmt: skip
VOCAB = [  # (needle in free text, platform code); first match wins
    ("q4", "q4"), ("notified", "notified"), ("globenewswire", "notified"), ("gcs-web", "notified"),
    ("static-files", "notified"), ("cloudfront", "equisolve"), ("equisolve", "equisolve"), ("s&p", "equisolve"),
    ("eqs", "eqs"), ("investis", "investis"), ("wordpress", "wordpress"), ("rmr", "rmr"), ("cision", "cision"),
    ("self-hosted", "self_hosted"), ("drupal", "self_hosted"), ("wix", "self_hosted"),
]  # fmt: skip


def norm_platform(text: str) -> str | None:
    t = (text or "").lower()
    return next((code for needle, code in VOCAB if needle in t), None)


def platform_of(hint: str, universe_platform: str) -> str:
    return norm_platform(hint) or norm_platform(universe_platform) or "other"


def strategy_of(status: str) -> str:
    return "headless" if status == "js_only" else "static_html"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("universe")
    ap.add_argument("probe")
    ap.add_argument("--date", required=True)
    ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()
    uni = list(csv.DictReader(Path(a.universe).read_text(encoding="utf-8-sig").splitlines()))
    probe = {
        r[0]: r for r in csv.reader(Path(a.probe).read_text(encoding="utf-8").splitlines()) if r and r[0] != "ticker"
    }
    old = {}
    if not a.reset and OUT.exists():
        old = {r["ticker"]: r for r in csv.DictReader(OUT.read_text(encoding="utf-8").splitlines())}
    rows = []
    for u in uni:
        t = u["ticker"].strip()
        p = probe.get(t)
        status = p[1] if p else "not_probed"
        row = {
            "ticker": t,
            "strategy": strategy_of(status),
            "platform": platform_of(p[5] if p else "", u.get("ir_platform", "")),
            "expects_supplemental": "0" if status == "no_supp" else "1",
            "probe_status": status,
            "probe_supp_links": p[2] if p else "",
            "probe_latest_period": p[4] if p else "",
            "probe_date": a.date if p else "",
            "notes": (p[6] if p else "not probed: no results page URL in the universe file").replace("\n", " ")[:300],
        }
        if t in old:
            row["strategy"] = old[t]["strategy"]
            row["expects_supplemental"] = old[t]["expects_supplemental"]
        rows.append(row)
    rows.sort(key=lambda r: r["ticker"])
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows to {OUT}")


if __name__ == "__main__":
    main()
