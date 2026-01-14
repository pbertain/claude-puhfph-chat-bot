#!/usr/bin/env python3
"""
Fetch and format METAR (aviation) weather reports.
"""
from __future__ import annotations

from typing import Iterable, List, Any

import requests
import json


def _c_to_f(temp_c: float | None) -> int | None:
    if temp_c is None:
        return None
    return int(round((temp_c * 9 / 5) + 32))


def _normalize_cover(value: str | None) -> str:
    return value if value else "CLR"


def _normalize_base(value: int | float | None) -> int:
    return 12000 if value is None else int(value)


def _format_wind(entry: dict) -> str:
    wind_dir = entry.get("wind_dir_degrees")
    wind_speed = entry.get("wind_speed_kt")
    wind_gust = entry.get("wind_gust_kt")

    wind_dir_val = 0 if wind_dir in (None, "") else int(wind_dir)
    wind_speed_val = 0 if wind_speed in (None, "") else int(wind_speed)
    wind_gust_val = 0 if wind_gust in (None, "") else int(wind_gust)

    if wind_dir_val == 0 and wind_speed_val == 0 and wind_gust_val == 0:
        return "CALM"

    dir_str = str(wind_dir_val) if wind_dir_val else "VRB"
    if wind_gust is not None:
        return f"{dir_str}@{wind_speed_val}G{wind_gust_val}"
    return f"{dir_str}@{wind_speed_val}"


def _format_ceiling(entry: dict) -> tuple[str, int]:
    covers = [
        entry.get("sky_cover"),
        entry.get("sky_cover2"),
        entry.get("sky_cover3"),
    ]
    bases = [
        entry.get("cloud_base_ft_agl"),
        entry.get("cloud_base_ft_agl2"),
        entry.get("cloud_base_ft_agl3"),
    ]

    # Determine cover to display: first non-null cover, else CLR
    cover_out = "CLR"
    for cover in covers:
        if cover:
            cover_out = cover
            break

    # Determine base: lowest BKN/OVC if present, else base for chosen cover
    bkn_ovc_bases: List[int] = []
    for cover, base in zip(covers, bases):
        if cover in {"BKN", "OVC"}:
            bkn_ovc_bases.append(_normalize_base(base))

    if bkn_ovc_bases:
        base_out = min(bkn_ovc_bases)
    else:
        # Use base for first non-null cover, otherwise default
        base_out = 12000
        for cover, base in zip(covers, bases):
            if cover:
                base_out = _normalize_base(base)
                break

    return _normalize_cover(cover_out), base_out


def _normalize_metar_data(data: Any) -> list[dict]:
    """Normalize API response into a list of dict entries."""
    if isinstance(data, list):
        # Expect list of dicts
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        # Single entry or wrapped list
        if "raw_text" in data and "station_id" in data:
            return [data]
        for key in ("data", "results", "metars"):
            value = data.get(key)
            if isinstance(value, list):
                return [d for d in value if isinstance(d, dict)]
        return []
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
            return _normalize_metar_data(parsed)
        except Exception:
            return []
    return []


def fetch_metars(stations: Iterable[str]) -> list[str]:
    """
    Fetch METARs for station IDs and return formatted strings.
    """
    station_list = [s.strip().lower() for s in stations if s.strip()]
    if not station_list:
        return []

    url = f"https://www.fli-rite.net/metars/{','.join(station_list)}"
    resp = requests.get(url, timeout=(10, 20))
    resp.raise_for_status()

    try:
        data = resp.json()
    except ValueError:
        # Fallback: try to parse as JSON string
        data = resp.text

    entries = _normalize_metar_data(data)
    results: list[str] = []

    for entry in entries:
        station_id = str(entry.get("station_id", "")).upper() or "UNK"
        flight_category = entry.get("flight_category") or "UNK"
        altim = entry.get("altim_in_hg")
        altim_str = f"{altim}" if altim is not None else "UNK"

        temp_f = _c_to_f(entry.get("temp_c"))
        dewpoint_f = _c_to_f(entry.get("dewpoint_c"))
        temp_str = f"{temp_f}" if temp_f is not None else "NA"
        dew_str = f"{dewpoint_f}" if dewpoint_f is not None else "NA"

        wind_str = _format_wind(entry)
        visibility = entry.get("visibility_statute_mi")
        if visibility is None:
            vis_str = "NA"
        else:
            vis_str = f"{float(visibility):.1f}"

        cover, base = _format_ceiling(entry)

        results.append(
            f"{station_id}-{flight_category}-{altim_str}-{temp_str}/{dew_str}-"
            f"{wind_str}-{vis_str}mi-{cover}|{base}ft"
        )

    return results
