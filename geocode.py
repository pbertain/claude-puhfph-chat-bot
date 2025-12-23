#!/usr/bin/env python3
"""
Geocoding functionality - consolidates Open-Meteo and Census geocoding.
"""
import re
import requests
from typing import Optional

import config


STATE_ABBR_RE = re.compile(r"^\s*([A-Za-z\.\s'-]+?)\s*,\s*([A-Za-z]{2})\s*$")


def normalize_text(s: str) -> str:
    """Normalize whitespace in text."""
    return " ".join((s or "").strip().split())


def parse_city_state(loc: str) -> tuple[str, str | None]:
    """
    Parse city and state from location string.
    Accepts:
      - "Davis, CA" -> ("Davis", "CA")
      - "Seattle, wa" -> ("Seattle", "WA")
      - "Davis" -> ("Davis", None)
    """
    loc = normalize_text(loc)
    m = STATE_ABBR_RE.match(loc)
    if m:
        city = normalize_text(m.group(1))
        st = m.group(2).upper()
        return city, st
    return loc, None


def open_meteo_geocode(loc: str, *, country_code: str | None = config.DEFAULT_COUNTRY_CODE) -> tuple[float, float, str]:
    """
    Open-Meteo geocoding:
      https://geocoding-api.open-meteo.com/v1/search?name=...&count=...&country_code=...
    Returns (lat, lon, display_name)
    """
    loc = normalize_text(loc)
    if not loc:
        raise ValueError("Empty location")

    city, st = parse_city_state(loc)

    # Search term: keep it human-ish; Open-Meteo handles fuzzy matching well.
    # For US: "Davis, CA" is a good hint.
    q = city if not st else f"{city}, {st}"

    params = {"name": q, "count": 10, "format": "json"}
    if country_code:
        params["country_code"] = country_code

    url = "https://geocoding-api.open-meteo.com/v1/search"
    r = requests.get(url, params=params, timeout=config.GEOCODE_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    results = data.get("results") or []
    if not results:
        raise ValueError(f"No Open-Meteo geocode match for: {loc}")

    # Pick best: if user gave a state, prefer matching admin1_code
    def score(item: dict) -> tuple[int, int]:
        # higher is better
        admin1 = (item.get("admin1_code") or "").upper()
        country = (item.get("country_code") or "").upper()
        s_state = 1 if (st and admin1 == st) else 0
        s_country = 1 if (country_code and country == (country_code or "").upper()) else 0
        return (s_state, s_country)

    best = max(results, key=lambda x: score(x))

    lat = float(best["latitude"])
    lon = float(best["longitude"])

    # Return just "City, State" format (e.g., "Davis, CA")
    name = best.get("name") or city
    admin1_code = best.get("admin1_code") or st or ""
    
    # If we have a state abbreviation, use it; otherwise use admin1 name
    if admin1_code and len(admin1_code) == 2:
        pretty = f"{name}, {admin1_code.upper()}"
    elif st:
        pretty = f"{name}, {st}"
    else:
        # Fallback to city only if no state
        pretty = name

    return lat, lon, pretty


def census_geocode_address_fallback(loc: str) -> tuple[float, float]:
    """
    Census one-line geocoder as a fallback (best for street addresses).
    """
    loc = " ".join((loc or "").strip().split())
    if not loc:
        raise ValueError("Empty location")

    candidates = [loc]
    if "usa" not in loc.lower() and "united states" not in loc.lower():
        candidates.append(f"{loc}, USA")
        candidates.append(f"{loc} United States")

    url = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
    for addr in candidates:
        params = {"address": addr, "benchmark": "Public_AR_Current", "format": "json"}
        r = requests.get(url, params=params, timeout=config.GEOCODE_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        matches = (data.get("result", {}) or {}).get("addressMatches", []) or []
        if matches:
            coords = matches[0].get("coordinates") or {}
            lon = float(coords["x"])
            lat = float(coords["y"])
            return lat, lon

    raise ValueError(f"No Census geocode match for: {loc}")


def geocode_location(loc: str) -> tuple[float, float, str]:
    """
    Try Open-Meteo (great for 'City, ST'), then fall back to Census (great for full addresses).
    Returns (lat, lon, display_name) where display_name is "City, State" format.
    """
    try:
        return open_meteo_geocode(loc, country_code=config.DEFAULT_COUNTRY_CODE)
    except Exception:
        lat, lon = census_geocode_address_fallback(loc)
        # For Census fallback, try to extract city, state from the input
        # Parse "City, State" or "Address, City, State" format
        parts = [p.strip() for p in loc.split(",")]
        if len(parts) >= 2:
            # Take last two parts as city, state
            city = parts[-2].strip()
            state = parts[-1].strip().upper()
            if len(state) == 2:
                return lat, lon, f"{city}, {state}"
        return lat, lon, loc

