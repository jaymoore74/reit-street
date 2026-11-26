"""Company universe import.

Primary source: the universe CSV maintained alongside this repo (reits.csv), columns
  ticker, company_name, cik, exchange, sector, reit_type, listing_note, ir_home_url,
  results_page_url, latest_supplemental_pdf_url, email_alerts_url, ir_platform,
  verified, scrape_notes, ...
Unknown columns are ignored. A missing CIK is resolved through SEC's ticker file.

The CSV itself is captured in the raw store, so the universe at any point in time can
be reconstructed. Companies are never deleted: with prune=True, companies missing from
the file are marked in_universe=0.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..core.db import Db, in_clause
from ..core.http import Fetcher, store_local_file
from ..core.rawstore import RawStore
from ..sources.sec import urls

log = logging.getLogger(__name__)

IR_COLUMNS = (
    ("ir_home_url", "ir_home", "html"),
    ("results_page_url", "results_page", "html"),
    ("latest_supplemental_pdf_url", "supplemental_pdf", "pdf"),
    ("email_alerts_url", "email_alerts", "email"),
)


@dataclass
class UniverseRow:
    ticker: str
    cik: int | None
    name: str | None
    exchange: str | None
    sector: str | None
    reit_type: str | None
    listing_note: str | None
    ir_platform: str | None
    verified: int | None
    scrape_notes: str | None
    ir_urls: dict[str, str]


def _clean(v: str | None) -> str | None:
    v = (v or "").strip()
    return v or None


def parse_universe_csv(data: bytes) -> list[UniverseRow]:
    out = []
    for row in csv.DictReader(io.StringIO(data.decode("utf-8-sig"))):
        row = {(k or "").strip().lower(): v for k, v in row.items()}
        t = _clean(row.get("ticker") or row.get("symbol"))
        if not t:
            continue
        cik = _clean(row.get("cik"))
        ver = (_clean(row.get("verified")) or "").upper()
        out.append(
            UniverseRow(
                ticker=t.upper(),
                cik=int(cik) if cik and cik.isdigit() else None,
                name=_clean(row.get("company_name") or row.get("name")),
                exchange=_clean(row.get("exchange")),
                sector=_clean(row.get("sector")),
                reit_type=_clean(row.get("reit_type")),
                listing_note=_clean(row.get("listing_note")),
                ir_platform=_clean(row.get("ir_platform")),
                verified={"Y": 1, "N": 0}.get(ver),
                scrape_notes=_clean(row.get("scrape_notes")),
                ir_urls={col: v for col, _t, _m in IR_COLUMNS if (v := _clean(row.get(col))) and v.startswith("http")},
            )
        )
    return out


def sec_ticker_map(fetcher: Fetcher) -> dict[str, dict[str, object]]:
    r = fetcher.get(urls.TICKERS_URL)
    assert r is not None
    data = r.json()
    fields = data["fields"]
    return {str(rec[fields.index("ticker")]).upper(): dict(zip(fields, rec)) for rec in data["data"]}


def import_universe(
    db: Db, store: RawStore, path: Path, *, run_id: int | None, fetcher: Fetcher | None, prune: bool = False
) -> dict[str, object]:
    sha, _ = store_local_file(db, store, path, source="universe_file", run_id=run_id, content_type="text/csv")
    rows = parse_universe_csv(Path(path).read_bytes())
    tmap: dict[str, dict[str, object]] = {}
    if fetcher is not None and any(r.cik is None for r in rows):
        tmap = sec_ticker_map(fetcher)
    label = f"{Path(path).name}@{sha[:12]}"
    seen, unresolved = [], []
    for r in rows:
        cik = r.cik
        if cik is None:
            hit = tmap.get(r.ticker) or tmap.get(r.ticker.replace(".", "-"))
            cik = int(str(hit["cik"])) if hit else None
        if cik is None:
            unresolved.append(r.ticker)
            continue
        db.execute(
            """INSERT INTO ref_company (cik, ticker, name, exchange, sector, reit_type, listing_note, ir_platform,
                                        in_universe, universe_source)
               VALUES (:cik, :t, :n, :ex, :sec, :rt, :ln, :irp, 1, :src) AS new
               ON DUPLICATE KEY UPDATE ticker=new.ticker, name=COALESCE(ref_company.name, new.name),
                 exchange=COALESCE(new.exchange, ref_company.exchange), sector=new.sector, reit_type=new.reit_type,
                 listing_note=new.listing_note, ir_platform=new.ir_platform, in_universe=1,
                 universe_source=new.universe_source""",
            {
                "cik": cik,
                "t": r.ticker,
                "n": r.name or r.ticker,
                "ex": r.exchange,
                "sec": r.sector,
                "rt": r.reit_type,
                "ln": r.listing_note,
                "irp": r.ir_platform,
                "src": label,
            },
        )
        cid = db.scalar("SELECT company_id FROM ref_company WHERE cik=:c", {"c": cik})
        db.execute(
            """INSERT INTO ref_company_ticker_history (company_id, ticker, exchange, first_seen, last_seen)
               VALUES (:c, :t, :ex, :d, :d) AS new ON DUPLICATE KEY UPDATE last_seen=new.last_seen""",
            {"c": cid, "t": r.ticker, "ex": r.exchange, "d": date.today()},
        )
        for col, stype, method in IR_COLUMNS:
            url = r.ir_urls.get(col)
            if not url:
                continue
            db.execute(
                """INSERT INTO ref_ir_source (company_id, source_type, url, check_method, verified, notes)
                   VALUES (:c, :st, :u, :m, :v, :n) AS new
                   ON DUPLICATE KEY UPDATE is_active=1, verified=new.verified, notes=new.notes""",
                {
                    "c": cid,
                    "st": stype,
                    "u": url,
                    "m": method,
                    "v": r.verified,
                    "n": (r.scrape_notes or "")[:1024] or None if stype == "results_page" else None,
                },
            )
        db.execute(
            """INSERT INTO ref_ir_source (company_id, source_type, url, check_method)
               VALUES (:c, 'sec_edgar', :u, 'sec') AS new ON DUPLICATE KEY UPDATE is_active=1""",
            {"c": cid, "u": urls.edgar_company_url(cik)},
        )
        seen.append(cik)
    pruned = 0
    if prune and seen:
        clause, params = in_clause("k", seen)
        pruned = db.execute(f"UPDATE ref_company SET in_universe=0 WHERE in_universe=1 AND cik NOT IN {clause}", params)
    db.commit()
    if unresolved:
        log.warning("tickers without CIK", extra={"tickers": ",".join(unresolved)})
    return {"rows": len(rows), "companies": len(seen), "unresolved": unresolved, "pruned": pruned, "file_sha256": sha}
