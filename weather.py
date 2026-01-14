#!/usr/bin/env python3
"""
Weather forecast functionality using wttr.in API.
"""
import time
import requests

import config


def wttr_forecast(city: str, state: str = None, country: str = "US", retries: int = 3) -> str:
    """
    Get weather forecast from wttr.in for a city.
    Returns formatted weather string.
    
    Args:
        city: City name
        state: State abbreviation (optional)
        country: Country code (default: "US")
        retries: Number of retry attempts (default: 3)
    """
    # Build location string
    if state:
        location = f"{city},{state},{country}"
    else:
        location = f"{city},{country}"
    
    url = f"https://wttr.in/{location}"
    
    # Try multiple formats - format 2 is more detailed, format 3 is simpler/one-line
    formats = ["2", "3"]
    
    last_error = None
    
    for attempt in range(retries):
        for fmt in formats:
            params = {"format": fmt}
            
            try:
                # Increase timeout: 20 seconds for connect, 30 seconds for read
                r = requests.get(url, params=params, timeout=(20, 30))
                r.raise_for_status()
                result = r.text.strip()
                
                # If we got a result, return it
                if result and result != "Unknown location":
                    return result
                    
            except requests.exceptions.Timeout as e:
                last_error = f"Timeout after {30 if attempt == retries - 1 else 20} seconds"
                if attempt < retries - 1:
                    # Exponential backoff: wait 1s, 2s, 4s
                    time.sleep(2 ** attempt)
                    continue
            except requests.exceptions.RequestException as e:
                last_error = str(e)
                # For non-timeout errors, try next format immediately
                continue
            except Exception as e:
                last_error = str(e)
                continue
        
        # If we've tried all formats and still failed, wait before retrying
        if attempt < retries - 1:
            time.sleep(2 ** attempt)
    
    # If all retries failed, raise with helpful error message
    raise ValueError(f"Weather lookup failed after {retries} attempts: {last_error}")

