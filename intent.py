#!/usr/bin/env python3
"""
LLM-based intent classification using Claude Haiku.
Falls back gracefully if the API is unavailable or quota is exceeded.
"""
import json
import os
from datetime import datetime, timezone
from typing import Optional

import database

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 150
DAILY_LIMIT = 200

SYSTEM_PROMPT = """You are an intent classifier for an iMessage chat bot. Given a user message, return a JSON object with the detected intent and any extracted parameters.

Available intents and their parameters:
- weather: {location?: string} — asking about weather/forecast/conditions
- movies: {zip_code?: string, city?: string, state?: string, radius?: int} — movie showtimes/listings
- schedule_weather: {time?: string, frequency?: "daily"|"once"} — schedule weather delivery
- schedule_movies: {time?: string, frequency?: "daily"|"once"} — schedule movie listings
- alarm: {title?: string, time?: string} — set alarm or reminder
- aviation: {station_ids?: string[]} — METAR/aviation weather
- zipcode: {zip_code?: string, city?: string, state?: string} — ZIP code lookup
- help: {} — asking for help or what the bot can do
- last_contact: {} — when did we last talk
- schedule_query: {} — what's scheduled
- location_update: {location: string} — updating their location
- name_change: {first?: string, last?: string} — changing their name
- unknown: {} — cannot determine intent

Rules:
- Return ONLY valid JSON, no explanation
- If unsure, return {"intent": "unknown"}
- Extract parameters when clearly stated
- For time, preserve the user's wording (e.g. "7am", "7:30pm PT")
- For location, preserve as-is (e.g. "Davis, CA", "Portland, OR")"""


def _get_client():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not HAS_ANTHROPIC:
        return None
    return anthropic.Anthropic(api_key=key)


def _check_daily_limit() -> bool:
    """Return True if we're under the daily limit."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    meta = database.get_global_meta("llm_usage")
    if not meta:
        return True
    try:
        data = json.loads(meta)
    except (json.JSONDecodeError, TypeError):
        return True
    if data.get("date") != today:
        return True
    return data.get("count", 0) < DAILY_LIMIT


def _increment_counter():
    """Increment the daily usage counter."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    meta = database.get_global_meta("llm_usage")
    try:
        data = json.loads(meta) if meta else {}
    except (json.JSONDecodeError, TypeError):
        data = {}
    if data.get("date") != today:
        data = {"date": today, "count": 0}
    data["count"] = data.get("count", 0) + 1
    database.set_global_meta("llm_usage", json.dumps(data))


def classify_intent(text: str) -> Optional[dict]:
    """
    Classify user intent using Claude Haiku.
    Returns dict with 'intent' key and optional parameters, or None if classification
    is unavailable (no API key, import error, over quota, API error).
    """
    if not HAS_ANTHROPIC:
        return None

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None

    if not _check_daily_limit():
        return None

    client = _get_client()
    if not client:
        return None

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
        )
        _increment_counter()
        result_text = response.content[0].text.strip()
        return json.loads(result_text)
    except Exception:
        return None
