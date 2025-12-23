#!/usr/bin/env python3
"""
Conversation state machine and message handling logic.
"""
import re
from datetime import datetime, timezone

import applescript_helpers
import config
import database
import geocode
import message_polling
import scheduler
import weather
from datetime import time as dt_time

# ------------ greeting ------------

def time_of_day_greeting(dt: datetime) -> str:
    """Get a time-appropriate greeting."""
    h = dt.hour
    if 5 <= h < 12:
        return "Good morning!"
    if 12 <= h < 17:
        return "Good afternoon!"
    if 17 <= h < 24:
        return "Good evening!"
    return "Good god it's late!"


# ------------ "how long has it been?" formatter ------------

def human_elapsed(seconds: int) -> str:
    """Format elapsed time in a human-readable way."""
    if seconds < 0:
        seconds = 0

    minute = 60
    hour = 60 * minute
    day = 24 * hour
    week = 7 * day
    month = 30 * day  # approximate

    parts = []
    for label, size in [("month", month), ("week", week), ("day", day), ("hour", hour), ("minute", minute)]:
        if seconds >= size:
            n = seconds // size
            seconds %= size
            parts.append(f"{n} {label}{'' if n == 1 else 's'}")

    return ", ".join(parts) if parts else "less than a minute"


# ------------ commands / parsing ------------

HELP_TEXT = """Commands:
• help / ?                          Show this help
• weather / wx                      Get your current forecast (based on saved location)
• I'm in <place> now                Update your location and get forecast
• send me the weather at 7am everyday    Schedule daily weather reports

Location examples (city/state is enough now):
• I'm in Davis, CA now
• I'm in Seattle, WA now
• I'm in Austin, TX now
• I'm in Paris now

Schedule examples:
• send me the weather at 7am everyday
• send me the weather at 7:30pm daily
• send me the weather at 7am once
"""

WEATHER_COMMANDS = {"weather", "wx", "forecast", "temp"}

IN_NOW_RE = re.compile(
    r"""^\s*(?:i'?m|i\s+am)?\s*in\s+(?P<loc>.+?)\s+now\s*$""",
    re.IGNORECASE,
)


def normalize_text(s: str) -> str:
    """Normalize whitespace in text."""
    return geocode.normalize_text(s)


def is_help(text: str) -> bool:
    """Check if text is a help command."""
    t = normalize_text(text).lower()
    return t in {"help", "?", "commands"}


def is_weather_cmd(text: str) -> bool:
    """Check if text is a weather command."""
    t = normalize_text(text).lower()
    return t in WEATHER_COMMANDS


def extract_in_now_location(text: str) -> str | None:
    """Extract location from "I'm in <place> now" pattern."""
    m = IN_NOW_RE.match(text or "")
    if not m:
        return None
    loc = normalize_text(m.group("loc"))
    return loc if loc else None


# ------------ conversation logic ------------

def display_first_name(handle_id: str) -> str:
    """Get display name for a handle (first name from DB, Contacts, or fallback)."""
    p = database.get_person(handle_id)
    if p.get("first_name"):
        return str(p["first_name"]).strip()
    cn = applescript_helpers.lookup_contact_name(handle_id)
    if cn:
        return cn.strip().split()[0]
    return "there"


def format_city_state(loc_label: str) -> str:
    """Format location as 'City, State' with city in title case."""
    # loc_label should be "City, State" format
    parts = [p.strip() for p in loc_label.split(",")]
    if len(parts) >= 2:
        city = parts[0].title()  # Title case: first letter uppercase, rest lowercase
        state = parts[1].upper()  # State abbreviation in uppercase
        return f"{city}, {state}"
    # Fallback if format is unexpected
    return loc_label.title()


def set_location(handle_id: str, loc: str) -> tuple[float, float, str]:
    """Set location for a person and update their state to ready."""
    lat, lon, pretty = geocode.geocode_location(loc)
    database.update_person(handle_id, location_text=pretty, lat=lat, lon=lon)
    database.set_state(handle_id, "ready")
    return lat, lon, pretty


def get_last_contact_info(handle_id: str) -> tuple[int, str] | None:
    """Get last contact time info. Returns (seconds, formatted_string) or None.
    Format: "[ Last contact: HH:MM PST  X mins ago / {epoch_time} ]"
    """
    meta = database.get_convo_meta(handle_id)
    last_incoming = database.parse_iso(meta.get("last_incoming_at") or "")
    
    if not last_incoming:
        return None
    
    now = datetime.now(timezone.utc)
    gap_seconds = int((now - last_incoming).total_seconds())
    
    if gap_seconds < 60:
        return None  # Too recent to show
    
    # Format time as HH:MM (24-hour format) in local timezone
    local_time = last_incoming.astimezone()
    time_str = local_time.strftime("%H:%M")
    
    # Get timezone abbreviation (PST, PDT, etc.)
    tz_abbr = local_time.strftime("%Z")
    if not tz_abbr:
        # Fallback if timezone abbreviation not available
        tz_abbr = local_time.strftime("%z")
        if tz_abbr:
            tz_abbr = f"UTC{tz_abbr}"
        else:
            tz_abbr = "PST"  # Default fallback
    
    # Format relative time as "X mins ago" or "X hours X mins ago"
    # Show minutes if less than 2 hours, otherwise show hours
    total_minutes = gap_seconds // 60
    hours = gap_seconds // 3600
    minutes = (gap_seconds % 3600) // 60
    
    if hours >= 2:
        # 2+ hours: show as "X hours" or "X hours X mins"
        if minutes > 0:
            relative_str = f"{hours} hours {minutes} mins"
        else:
            relative_str = f"{hours} hours"
    else:
        # Less than 2 hours: show as "X mins"
        relative_str = f"{total_minutes} mins"
    
    # Get epoch time
    epoch_time = int(last_incoming.timestamp())
    
    formatted = f"[ Last contact: {time_str} {tz_abbr}  {relative_str} ago / {epoch_time} ]"
    
    return (gap_seconds, formatted)


def reply_weather(handle_id: str, loc_label: str, lat: float, lon: float) -> None:
    """Send a weather forecast reply with last contact info."""
    # Parse location for wttr.in
    # loc_label should now be "City, State" format
    parts = [p.strip() for p in loc_label.split(",")]
    city = parts[0] if parts else loc_label
    state = parts[1] if len(parts) > 1 and len(parts[1]) == 2 else None
    country = "US"  # Default to US
    
    try:
        wx = weather.wttr_forecast(city, state, country)
    except Exception as e:
        wx = f"Weather lookup failed ({e})"
    
    # Format location with proper case
    city_state = format_city_state(loc_label)
    
    # Build message - format: "City, State Forecast:\n\n{weather}\n\n[ Last contact: ... ]"
    message = f"{city_state} Forecast:\n\n{wx}"
    
    # Add last contact info if available (with empty line before it)
    last_contact = get_last_contact_info(handle_id)
    if last_contact:
        _, formatted = last_contact
        message += f"\n\n{formatted}"
    
    applescript_helpers.send_imessage(handle_id, message)


def maybe_send_welcome_back(handle_id: str) -> None:
    """Send a welcome back message if appropriate."""
    meta = database.get_convo_meta(handle_id)
    last_incoming = database.parse_iso(meta.get("last_incoming_at") or "")
    last_welcome = database.parse_iso(meta.get("last_welcome_at") or "")

    if not last_incoming:
        return

    now = datetime.now(timezone.utc)
    gap = int((now - last_incoming).total_seconds())

    if gap < config.WELCOME_BACK_GAP_SECONDS:
        return
    if last_welcome and last_welcome > last_incoming:
        return

    first = display_first_name(handle_id)
    elapsed = human_elapsed(gap)
    applescript_helpers.send_imessage(handle_id, f"Welcome back, {first}. It's been {elapsed} since you last texted.")
    database.set_convo_meta(handle_id, last_welcome_at=database.now_iso())


def handle_incoming(msg: message_polling.Incoming) -> None:
    """Handle an incoming message based on conversation state."""
    database.ensure_person_row(msg.handle_id)

    person = database.get_person(msg.handle_id)
    database.update_person(msg.handle_id, last_seen_at=database.now_iso())

    # Don't send separate welcome back - it's now included in weather replies
    # maybe_send_welcome_back(msg.handle_id)
    database.set_convo_meta(msg.handle_id, last_incoming_at=database.now_iso())

    if not msg.text:
        return

    if is_help(msg.text):
        applescript_helpers.send_imessage(msg.handle_id, HELP_TEXT)
        return

    in_now_loc = extract_in_now_location(msg.text)
    if in_now_loc:
        try:
            lat, lon, pretty = set_location(msg.handle_id, in_now_loc)
        except Exception as e:
            applescript_helpers.send_imessage(msg.handle_id, f"Sorry — I couldn't find that location. Try: \"Davis, CA\". ({e})")
            return
        reply_weather(msg.handle_id, pretty, lat, lon)
        return

    state = database.get_state(msg.handle_id)

    if state == "need_first":
        cn = applescript_helpers.lookup_contact_name(msg.handle_id)
        if cn:
            parts = cn.split()
            first = parts[0]
            last = " ".join(parts[1:]) if len(parts) > 1 else ""
            database.update_person(msg.handle_id, first_name=first, last_name=last)
            database.set_state(msg.handle_id, "need_location")
            applescript_helpers.send_imessage(msg.handle_id, f"Hi {first}! What city and state are you in? (e.g., Davis, CA)")
            return

        applescript_helpers.send_imessage(msg.handle_id, "Hi! What's your first name?")
        database.set_state(msg.handle_id, "need_last")
        return

    if state == "need_last":
        p = database.get_person(msg.handle_id)
        if not p.get("first_name"):
            first = normalize_text(msg.text)
            database.update_person(msg.handle_id, first_name=first)
            applescript_helpers.send_imessage(msg.handle_id, f"Nice to meet you, {first}. What's your last name?")
            return
        else:
            last = normalize_text(msg.text)
            database.update_person(msg.handle_id, last_name=last)
            database.set_state(msg.handle_id, "need_location")
            first = display_first_name(msg.handle_id)
            applescript_helpers.send_imessage(msg.handle_id, f"Thanks {first}! What city and state are you in? (e.g., Davis, CA)")
            return

    if state == "need_location":
        loc = normalize_text(msg.text)
        try:
            _, _, pretty = set_location(msg.handle_id, loc)
        except Exception as e:
            applescript_helpers.send_imessage(msg.handle_id, f"Sorry — I couldn't find that location. Try: \"Davis, CA\". ({e})")
            return

        first = display_first_name(msg.handle_id)
        applescript_helpers.send_imessage(msg.handle_id, f"Thanks {first}! Saved your location as: {pretty}. Text \"weather\" or \"wx\" anytime.")
        return

    # ready state:
    if is_weather_cmd(msg.text):
        p = database.get_person(msg.handle_id)
        loc = p.get("location_text")
        lat = p.get("lat")
        lon = p.get("lon")
        if not loc or lat is None or lon is None:
            database.set_state(msg.handle_id, "need_location")
            applescript_helpers.send_imessage(msg.handle_id, f"What city and state are you in, {display_first_name(msg.handle_id)}? (e.g., Davis, CA)")
            return
        reply_weather(msg.handle_id, loc, float(lat), float(lon))
        return

    # Check for scheduler commands
    schedule_info = scheduler.parse_schedule_command(msg.text)
    if schedule_info:
        p = database.get_person(msg.handle_id)
        loc = p.get("location_text")
        lat = p.get("lat")
        lon = p.get("lon")
        if not loc or lat is None or lon is None:
            applescript_helpers.send_imessage(msg.handle_id, f"I need your location first. What city and state are you in, {display_first_name(msg.handle_id)}? (e.g., Davis, CA)")
            return
        
        try:
            # Handle relative time scheduling
            if "relative_delta" in schedule_info:
                schedule_id = scheduler.add_scheduled_message(
                    msg.handle_id,
                    schedule_info["message_type"],
                    schedule_type=schedule_info["schedule"],
                    relative_delta=schedule_info["relative_delta"],
                )
                first = display_first_name(msg.handle_id)
                delta = schedule_info["relative_delta"]
                minutes = int(delta.total_seconds() / 60)
                hours = int(delta.total_seconds() / 3600)
                if hours > 0:
                    time_desc = f"{hours} hour{'s' if hours != 1 else ''}"
                    if minutes % 60 > 0:
                        time_desc += f" {minutes % 60} minute{'s' if minutes % 60 != 1 else ''}"
                else:
                    time_desc = f"{minutes} minute{'s' if minutes != 1 else ''}"
                # Format city name in title case
                city_state = format_city_state(loc)
                applescript_helpers.send_imessage(
                    msg.handle_id,
                    f"Weather for {city_state} will be sent in {time_desc}."
                )
            else:
                # Handle absolute time scheduling
                schedule_id = scheduler.add_scheduled_message(
                    msg.handle_id,
                    schedule_info["message_type"],
                    schedule_time=schedule_info["time"],
                    schedule_type=schedule_info["schedule"],
                    tz_str=schedule_info.get("timezone"),
                )
                first = display_first_name(msg.handle_id)
                time_str = schedule_info["time"].strftime("%I:%M %p").lstrip("0")
                tz_part = ""
                if schedule_info.get("timezone"):
                    # Extract timezone abbreviation from timezone string
                    tz_abbr = None
                    for abbr, tz_name in scheduler.TZ_MAP.items():
                        if tz_name == schedule_info["timezone"]:
                            tz_abbr = abbr.upper()
                            break
                    if tz_abbr:
                        tz_part = f" {tz_abbr}"
                
                # Format city name in title case
                city_state = format_city_state(loc)
                if schedule_info["schedule"] == scheduler.SCHEDULE_DAILY:
                    applescript_helpers.send_imessage(
                        msg.handle_id,
                        f"Weather for {city_state} will be sent at {time_str}{tz_part} every day."
                    )
                else:
                    applescript_helpers.send_imessage(
                        msg.handle_id,
                        f"Weather for {city_state} will be sent at {time_str}{tz_part}."
                    )
        except Exception as e:
            applescript_helpers.send_imessage(
                msg.handle_id,
                f"Sorry, I couldn't set up that schedule. ({e})"
            )
        return

    return


def execute_scheduled_weather(handle_id: str) -> None:
    """Execute a scheduled weather message for a handle."""
    p = database.get_person(handle_id)
    loc = p.get("location_text")
    lat = p.get("lat")
    lon = p.get("lon")
    if not loc or lat is None or lon is None:
        # Skip if location not set
        return
    reply_weather(handle_id, loc, float(lat), float(lon))

