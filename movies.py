#!/usr/bin/env python3
"""
Movie showtimes functionality using MoviePuff API.
"""
import time
import requests

import config

MOVIEPUFF_BASE_URL = "https://moviepuff.net/curl/v1"


def movie_showtimes(city: str = None, state: str = None, zip_code: str = None,
                    radius: int = 10, retries: int = 3) -> str:
    """
    Get movie showtimes from MoviePuff API.
    Returns pre-formatted plain text showtime listing.

    Provide either zip_code OR city+state. zip_code takes priority.
    """
    url = f"{MOVIEPUFF_BASE_URL}/showtimes"
    params = {"radius": radius}

    if zip_code:
        params["zip"] = zip_code
    elif city and state:
        params["city"] = city
        params["state"] = state
    elif city:
        params["city"] = city
    else:
        raise ValueError("Provide either a zip code or city (and state)")

    last_error = None

    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=(20, 30))
            r.raise_for_status()
            result = r.content.decode('utf-8').strip()
            if result:
                return result
        except requests.exceptions.Timeout:
            last_error = "Timeout"
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
        except requests.exceptions.RequestException as e:
            last_error = str(e)
            continue
        except Exception as e:
            last_error = str(e)
            continue

        if attempt < retries - 1:
            time.sleep(2 ** attempt)

    raise ValueError(f"Movie lookup failed after {retries} attempts: {last_error}")
