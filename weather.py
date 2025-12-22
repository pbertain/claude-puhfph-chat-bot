#!/usr/bin/env python3
"""
Weather forecast functionality using NWS API.
"""
import requests

import config


def nws_forecast_one_liner(lat: float, lon: float) -> str:
    """Get a one-line weather forecast from NWS for the given coordinates."""
    headers = {
        "User-Agent": config.NWS_USER_AGENT,
        "Accept": "application/geo+json, application/json",
    }
    points_url = f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}"
    p = requests.get(points_url, headers=headers, timeout=10)
    p.raise_for_status()
    pj = p.json()

    forecast_url = ((pj.get("properties") or {}).get("forecast"))
    if not forecast_url:
        raise ValueError("NWS points response missing properties.forecast")

    f = requests.get(forecast_url, headers=headers, timeout=10)
    f.raise_for_status()
    fj = f.json()

    periods = ((fj.get("properties") or {}).get("periods")) or []
    if not periods:
        raise ValueError("NWS forecast response missing periods")

    first = periods[0]
    name = first.get("name", "Forecast")
    temp = first.get("temperature")
    unit = first.get("temperatureUnit")
    short = first.get("shortForecast", "")
    return f"{name}: {temp}{unit}. {short}".strip()

