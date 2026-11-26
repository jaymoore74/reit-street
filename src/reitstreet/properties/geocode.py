"""Geocode property addresses with the free US Census geocoder, cached by address key.

One GET per distinct normalized address (onelineaddress, JSON), through a Fetcher so each
answer lands in the raw store. ref_geocode_cache keeps match, no_match and tie answers, so
an address is asked once. Census interpolates along street ranges: precision 'street'.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from ..core.errors import TransientError

if TYPE_CHECKING:
    from ..core.db import Db
    from ..core.http import Fetcher

log = logging.getLogger(__name__)
CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
PROVIDER = "census"


def census_url(one_line: str) -> str:
    return CENSUS_URL + "?" + urlencode({"address": one_line, "benchmark": "Public_AR_Current", "format": "json"})


def one_line(street: str | None, city: str | None, state: str | None, postal: str | None) -> str:
    tail = " ".join(x for x in (state, postal) if x)
    return ", ".join(x for x in (street, city, tail) if x)


def parse_census(body: bytes) -> dict[str, Any]:
    """-> {status, matched_address, latitude, longitude, precision_level, provider_ref, state}"""
    data = json.loads(body)
    matches = ((data.get("result") or {}).get("addressMatches")) or []
    if not matches:
        return {"status": "no_match", "precision_level": "none"}
    if len(matches) > 1:
        return {"status": "tie", "precision_level": "none", "matched_address": matches[0].get("matchedAddress")}
    m = matches[0]
    c = m.get("coordinates") or {}
    comp = m.get("addressComponents") or {}
    return {
        "status": "match",
        "matched_address": (m.get("matchedAddress") or "")[:255] or None,
        "latitude": round(float(c["y"]), 6) if c.get("y") is not None else None,
        "longitude": round(float(c["x"]), 6) if c.get("x") is not None else None,
        "precision_level": "street",
        "provider_ref": str((m.get("tigerLine") or {}).get("tigerLineId") or "")[:64] or None,
        "state": comp.get("state"),
    }


def geocode_pending(db: Db, fetcher: Fetcher, *, limit: int | None = None, run_id: int | None = None) -> dict[str, int]:
    todo = db.query(
        """SELECT r.address_key, MIN(r.address_line) AS address_line, MIN(r.city) AS city, MIN(r.state) AS state,
                  MIN(r.postal_code) AS postal_code
           FROM stg_property_record r
           LEFT JOIN ref_geocode_cache g ON g.address_key = r.address_key
           WHERE r.retired_at IS NULL AND r.address_key IS NOT NULL AND g.address_key IS NULL
             AND (r.country IS NULL OR r.country = 'US')
           GROUP BY r.address_key"""
        + (f" LIMIT {int(limit)}" if limit else "")
    )
    res = {"addresses": len(todo), "match": 0, "no_match": 0, "tie": 0, "error": 0}
    for t in todo:
        addr = one_line(t["address_line"], t["city"], t["state"], t["postal_code"])
        try:
            r = fetcher.get(census_url(addr))
            assert r is not None
            g = parse_census(r.body)
            sha = r.sha256
        except (TransientError, ValueError, KeyError) as e:
            log.warning("geocode failed", extra={"address_key": t["address_key"], "err": str(e)[:200]})
            g, sha = {"status": "error", "precision_level": "none"}, None
        res[g["status"]] += 1
        if g["status"] == "error":
            continue  # ask again next run
        db.execute(
            """INSERT INTO ref_geocode_cache (address_key, input_address, provider, status, match_type, matched_address,
                 latitude, longitude, precision_level, provider_ref, raw_sha256)
               VALUES (:k, :a, :p, :s, NULL, :m, :lat, :lon, :pr, :ref, :sha) AS new
               ON DUPLICATE KEY UPDATE status=new.status, matched_address=new.matched_address, latitude=new.latitude,
                 longitude=new.longitude, precision_level=new.precision_level, provider_ref=new.provider_ref,
                 raw_sha256=new.raw_sha256, geocoded_at=CURRENT_TIMESTAMP(6)""",
            {
                "k": t["address_key"],
                "a": addr[:512],
                "p": PROVIDER,
                "s": g["status"],
                "m": g.get("matched_address"),
                "lat": g.get("latitude"),
                "lon": g.get("longitude"),
                "pr": g["precision_level"],
                "ref": g.get("provider_ref"),
                "sha": sha,
            },
        )
        db.commit()
    # properties at newly geocoded addresses pick up the point
    from .build import refresh_properties

    pids = [
        int(r["property_id"])
        for r in db.query(
            """SELECT p.property_id FROM wh_property p JOIN ref_geocode_cache g ON g.address_key = p.address_key
               WHERE p.latitude IS NULL AND g.status = 'match' AND p.status <> 'merged'"""
        )
    ]
    res["properties_located"] = refresh_properties(db, pids, run_id)
    db.commit()
    return res
