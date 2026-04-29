#!/usr/bin/env python3
"""
ZIP code lookup and validation using ZipPuff API.
"""
import requests

ZIPPUFF_BASE_URL = "https://www.zippuff.net/api/v1"


def zip_to_city(zip_code: str, timeout: int = 10) -> dict:
    """
    Look up city/state by ZIP code.
    Returns dict with keys: zipcode, city, state.
    Raises ValueError if ZIP is invalid or not found.
    """
    r = requests.get(f"{ZIPPUFF_BASE_URL}/zip2city/{zip_code}", timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if not data.get("city"):
        raise ValueError(f"No city found for ZIP {zip_code}")
    return data


def city_to_zips(city: str, state: str, timeout: int = 10) -> list[str]:
    """
    Look up ZIP codes by city and state.
    Returns list of ZIP code strings.
    Raises ValueError if no results found.
    """
    r = requests.get(f"{ZIPPUFF_BASE_URL}/city2zip/{city}+{state}", timeout=timeout)
    r.raise_for_status()
    data = r.json()
    zips = data.get("zip_codes", [])
    if not zips:
        raise ValueError(f"No ZIP codes found for {city}, {state}")
    return zips


def validate_zip(zip_code: str, timeout: int = 10) -> dict:
    """
    Validate a ZIP code.
    Returns dict with keys: zipcode, city, state, valid.
    """
    r = requests.get(f"{ZIPPUFF_BASE_URL}/validate/{zip_code}", timeout=timeout)
    r.raise_for_status()
    return r.json()
