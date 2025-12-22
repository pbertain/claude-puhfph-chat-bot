#!/usr/bin/env python3
"""
geocode_utils.py

Geocoding helpers for iMessage listener:
- Parse user-entered location text (ZIP, City, ST, City, State, City, Country)
- Resolve to lat/lon with:
    1) US Census geocoder (great for US addresses / ZIP / City, ST)
    2) Nominatim fallback (worldwide city search, rate-limited)
- Handle ambiguity by returning multiple candidates
- Simple sqlite cache to reduce requests
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests


# --------- configuration ---------

DEFAULT_CACHE_DB = Path.home() / ".imessage_geocode_cache.sqlite3"

# Nominatim policy: include a descriptive UA
NOMINATIM_UA = "imessage-autoreply-bot/1.3 (contact: you@example.com)"

# be kind to Nominatim
NOMINATIM_MIN_DELAY_SECONDS = 1.1

# --------- types ---------

@dataclass
class GeoCandidate:
    label: str
    lat: float
    lon: float
    source: str  # "census" | "nominatim"


class GeocodeError(Exception):
    pass


class AmbiguousLocation(Exception):
    def __init__(self, query: str, candidates: list[GeoCandidate]):
        super().__init__(f"Ambiguous location for {query}")
        self.query = query
        self.candidates = candidates


# --------- cache ---------

def _db_connect(cache_db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(cache_db)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS geocode_cache (
          key TEXT PRIMARY KEY,
          response_json TEXT NOT NULL,
          created_at INTEGER NOT NULL
        )
        """
    )
    con.commit()
    return con


def _cache_key(provider: str, q: str) -> str:
    h = hashlib.sha256((provider + "\n" + q.strip().lower()).encode("utf-8")).hexdigest()
    return f"{provider}:{h}"


def cache_get(cache_db: Path, provider: str, q: str, max_age_days: int = 180) -> Optional[dict]:
    con = _db_connect(cache_db)
    key = _cache_key(provider, q)
    row = con.execute("SELECT response_json, created_at FROM geocode_cache WHERE key = ?", (key,)).fetchone()
    con.close()
    if not row:
        return None
    resp_json, created_at = row
    age_seconds = int(time.time()) - int(created_at)
    if age_seconds > max_age_days * 86400:
        return None
    try:
        return json.loads(resp_json)
    except Exception:
        return None


def cache_put(cache_db: Path, provider: str, q: str, obj: dict) -> None:
    con = _db_connect(cache_db)
    key = _cache_key(provider, q)
    con.execute(
        "INSERT OR REPLACE INTO geocode_cache(key, response_json, created_at) VALUES(?, ?, ?)",
        (key, json.dumps(obj), int(time.time())),
    )
    con.commit()
    con.close()


# --------- parsing ---------

_US_STATE_MAP = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}

_ZIP_RE = re.compile(r"^\s*(\d{5})(?:-\d{4})?\s*$")


def normalize_text(s: str) -> str:
    return " ".join((s or "").strip().split())


def parse_location(text: str) -> dict | None:
    """
    Returns an intent dict:
      {"type":"zip","zip":"95616"}
      {"type":"city_region","city":"Davis","region":"CA"}
      {"type":"city_only","city":"Portland"}
    """
    t = normalize_text(text)
    if not t:
        return None

    m = _ZIP_RE.match(t)
    if m:
        return {"type": "zip", "zip": m.group(1)}

    # "City, ST" or "City, State" or "City, Country"
    if "," in t:
        left, right = [x.strip() for x in t.split(",", 1)]
        if left and right:
            region = right
            if len(region) != 2:
                reg_norm = _US_STATE_MAP.get(region.lower(), region)
            else:
                reg_norm = region.upper()
            return {"type": "city_region", "city": left, "region": reg_norm}

    return {"type": "city_only", "city": t}


# --------- geocoders ---------

def census_geocode_oneline(query: str, cache_db: Path) -> list[GeoCandidate]:
    cached = cache_get(cache_db, "census", query)
    if cached is None:
        url = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
        params = {"address": query, "benchmark": "Public_AR_Current", "format": "json"}
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        cached = r.json()
        cache_put(cache_db, "census", query, cached)

    matches = (cached.get("result", {}) or {}).get("addressMatches", []) or []
    out: list[GeoCandidate] = []
    for m in matches[:5]:
        coords = m.get("coordinates") or {}
        if "x" in coords and "y" in coords:
            label = (m.get("matchedAddress") or query).strip()
            out.append(GeoCandidate(label=label, lat=float(coords["y"]), lon=float(coords["x"]), source="census"))
    return out


_last_nominatim_call = 0.0


def nominatim_search(query: str, cache_db: Path, limit: int = 5) -> list[GeoCandidate]:
    global _last_nominatim_call

    cached = cache_get(cache_db, "nominatim", query)
    if cached is None:
        # rate limit
        now = time.time()
        delta = now - _last_nominatim_call
        if delta < NOMINATIM_MIN_DELAY_SECONDS:
            time.sleep(NOMINATIM_MIN_DELAY_SECONDS - delta)
        _last_nominatim_call = time.time()

        url = "https://nominatim.openstreetmap.org/search"
        params = {
            "q": query,
            "format": "jsonv2",
            "limit": str(limit),
            "addressdetails": "1",
        }
        headers = {"User-Agent": NOMINATIM_UA}
        r = requests.get(url, params=params, headers=headers, timeout=12)
        r.raise_for_status()
        cached = {"results": r.json()}
        cache_put(cache_db, "nominatim", query, cached)

    results = cached.get("results") or []
    out: list[GeoCandidate] = []
    for item in results[:limit]:
        try:
            label = (item.get("display_name") or query).strip()
            out.append(GeoCandidate(label=label, lat=float(item["lat"]), lon=float(item["lon"]), source="nominatim"))
        except Exception:
            continue
    return out


def resolve_location_to_candidates(raw_text: str, cache_db: Path = DEFAULT_CACHE_DB) -> tuple[str, list[GeoCandidate]]:
    """
    Returns (normalized_label, candidates).
    normalized_label is a user-friendly label (often the query).
    """
    intent = parse_location(raw_text)
    if not intent:
        raise GeocodeError("Empty location")

    if intent["type"] == "zip":
        q = intent["zip"]
        # Census handles ZIP great
        cands = census_geocode_oneline(q, cache_db)
        if cands:
            return q, cands
        # fallback
        cands = nominatim_search(q, cache_db)
        return q, cands

    if intent["type"] == "city_region":
        q = f'{intent["city"]}, {intent["region"]}'
        # Try census first, then nominatim
        cands = census_geocode_oneline(q, cache_db)
        if not cands:
            cands = nominatim_search(q, cache_db)
        return q, cands

    # city only: nominatim first (worldwide), census second sometimes works too
    q = intent["city"]
    cands = nominatim_search(q, cache_db)
    if not cands:
        cands = census_geocode_oneline(q, cache_db)
    return q, cands


def pick_best_or_raise(raw_text: str, cache_db: Path = DEFAULT_CACHE_DB, ambiguity_threshold: int = 1) -> GeoCandidate:
    """
    If exactly one candidate -> return it.
    If 0 -> raise GeocodeError
    If >1 -> raise AmbiguousLocation (caller should disambiguate)
    """
    label, cands = resolve_location_to_candidates(raw_text, cache_db=cache_db)
    if not cands:
        raise GeocodeError(f"No match for: {label}")
    if len(cands) > ambiguity_threshold:
        raise AmbiguousLocation(label, cands)
    return cands[0]

