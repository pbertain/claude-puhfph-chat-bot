#!/usr/bin/env python3
"""
Weather forecast functionality using wttr.in API.
"""
import requests

import config


def wttr_forecast(city: str, state: str = None, country: str = "US") -> str:
    """
    Get weather forecast from wttr.in for a city.
    Returns formatted weather string.
    """
    # Build location string
    if state:
        location = f"{city},{state},{country}"
    else:
        location = f"{city},{country}"
    
    url = f"https://wttr.in/{location}"
    params = {"format": "2"}
    
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        return r.text.strip()
    except Exception as e:
        raise ValueError(f"Weather lookup failed: {e}")

