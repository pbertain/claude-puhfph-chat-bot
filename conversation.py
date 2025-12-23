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
• what do i have scheduled          Show your scheduled messages
• last contact                      Show when we last talked

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
LAST_CONTACT_COMMANDS = {"when did we last talk", "last contact", "when did we last contact", "last time we talked"}
SCHEDULE_QUERY_COMMANDS = {"what do i have scheduled", "what's scheduled", "show my schedule", "list schedule", "what schedules", "my schedules"}

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


def is_last_contact_cmd(text: str) -> bool:
    """Check if text is asking for last contact info."""
    t = normalize_text(text).lower()
    return any(cmd in t for cmd in LAST_CONTACT_COMMANDS)


def is_schedule_query_cmd(text: str) -> bool:
    """Check if text is asking about scheduled messages."""
    t = normalize_text(text).lower()
    return any(cmd in t for cmd in SCHEDULE_QUERY_COMMANDS)


def extract_in_now_location(text: str) -> str | None:
    """Extract location from "I'm in <place> now" pattern."""
    m = IN_NOW_RE.match(text or "")
    if not m:
        return None
    loc = normalize_text(m.group("loc"))
    return loc if loc else None


def extract_weather_for_location(text: str) -> str | None:
    """Extract location from "send me the weather for <location>" pattern."""
    # Patterns: "weather for Portland, OR", "weather for Portland OR", etc.
    patterns = [
        r'(?:send|text)\s+(?:me\s+)?(?:the\s+)?weather\s+for\s+(.+?)(?:\s+at\s+|\s+in\s+|\s+everyday|\s+daily|\s+once|$)',
        r'weather\s+for\s+(.+?)(?:\s+at\s+|\s+in\s+|\s+everyday|\s+daily|\s+once|$)',
    ]
    
    text_lower = text.lower()
    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if match:
            loc = match.group(1).strip()
            # Remove trailing punctuation
            loc = loc.rstrip('.,!?')
            return normalize_text(loc) if loc else None
    
    return None


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


def extract_city_name(loc_label: str) -> str:
    """Extract just the city name from location text (handles addresses like '1602 Madrone Ln, DAVIS')."""
    # Split by comma and find the city name
    parts = [p.strip() for p in loc_label.split(",")]
    
    if len(parts) >= 2:
        # Look for ZIP code (5 digits) first, then state abbreviation (2 uppercase letters)
        # City is typically right before the state
        for i in range(len(parts) - 1, -1, -1):
            part = parts[i].strip()
            # Check if this part is a ZIP code (5 digits)
            if len(part) == 5 and part.isdigit():
                # ZIP found - city is 2 parts back (before state)
                if i >= 2:
                    city = parts[i - 2].strip()
                    # If city part has numbers, it might be an address - look further back
                    if any(char.isdigit() for char in city):
                        # Look backwards for a part without numbers
                        for j in range(i - 3, -1, -1):
                            candidate = parts[j].strip()
                            if not any(char.isdigit() for char in candidate):
                                city = candidate
                                break
                    return city.title()
            # Check if this part is a state abbreviation (2 uppercase letters)
            elif len(part) == 2 and part.isalpha() and part.isupper():
                # State found - city is the previous part
                if i > 0:
                    city = parts[i - 1].strip()
                    # If city part has numbers, it might be an address - look further back
                    if any(char.isdigit() for char in city):
                        # Look backwards for a part without numbers
                        for j in range(i - 2, -1, -1):
                            candidate = parts[j].strip()
                            if not any(char.isdigit() for char in candidate):
                                city = candidate
                                break
                    return city.title()
        
        # If no state/ZIP found, try to find city by looking for parts without numbers
        # Usually city is one of the later parts (not the first which is often street address)
        for part in reversed(parts):
            if not any(char.isdigit() for char in part):
                return part.title()
        
        # Fallback: return second-to-last part (often city)
        return parts[-2].title() if len(parts) >= 2 else parts[0].title()
    
    # Fallback: return first part or whole string
    return parts[0].title() if parts else loc_label.title()


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


def reply_weather(handle_id: str, loc_label: str, lat: float, lon: float, include_last_contact: bool = False) -> None:
    """Send a weather forecast reply. Optionally include last contact info."""
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
    
    # Extract just the city name (not full address or state)
    city_name = extract_city_name(loc_label)
    
    # Build message - format: "City Forecast:\n\n{weather}"
    message = f"{city_name} Forecast:\n\n{wx}"
    
    # Add last contact info only if requested
    if include_last_contact:
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
    # Check for "weather for [location]" pattern first
    weather_for_loc = extract_weather_for_location(msg.text)
    if weather_for_loc:
        # User specified a location - geocode it and send weather (don't update stored location)
        try:
            lat, lon, pretty = geocode.geocode_location(weather_for_loc)
            reply_weather(msg.handle_id, pretty, lat, lon)
            return
        except Exception as e:
            applescript_helpers.send_imessage(msg.handle_id, f"Sorry — I couldn't find that location. Try: \"Portland, OR\". ({e})")
            return
    
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
                # Extract just the city name (not full address)
                city_name = extract_city_name(loc)
                # Use "mins" instead of "minutes" for consistency
                time_desc_short = f"{minutes} min{'s' if minutes != 1 else ''}" if minutes < 60 else time_desc
                applescript_helpers.send_imessage(
                    msg.handle_id,
                    f"Weather for {city_name} will be sent in {time_desc_short}."
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
                
                # Extract just the city name (not full address)
                city_name = extract_city_name(loc)
                if schedule_info["schedule"] == scheduler.SCHEDULE_DAILY:
                    applescript_helpers.send_imessage(
                        msg.handle_id,
                        f"Weather for {city_name} will be sent at {time_str}{tz_part} every day."
                    )
                else:
                    applescript_helpers.send_imessage(
                        msg.handle_id,
                        f"Weather for {city_name} will be sent at {time_str}{tz_part}."
                    )
        except Exception as e:
            applescript_helpers.send_imessage(
                msg.handle_id,
                f"Sorry, I couldn't set up that schedule. ({e})"
            )
        return
    
    # Check for last contact query
    if is_last_contact_cmd(msg.text):
        last_contact = get_last_contact_info(msg.handle_id)
        if last_contact:
            _, formatted = last_contact
            applescript_helpers.send_imessage(msg.handle_id, formatted)
        else:
            applescript_helpers.send_imessage(msg.handle_id, "We haven't talked recently enough to show last contact info.")
        return
    
    # Check for schedule query
    if is_schedule_query_cmd(msg.text):
        schedules = scheduler.get_user_scheduled_messages(msg.handle_id)
        if not schedules:
            applescript_helpers.send_imessage(msg.handle_id, "You don't have any scheduled messages.")
            return
        
        now = datetime.now(timezone.utc)
        messages = []
        for sched in schedules:
            next_run = database.parse_iso(sched["next_run_at"])
            if not next_run:
                continue
            
            # Format next run time
            local_next = next_run.astimezone()
            time_str = local_next.strftime("%I:%M %p").lstrip("0")
            date_str = local_next.strftime("%b %d")
            
            if sched["schedule_type"] == scheduler.SCHEDULE_DAILY:
                if sched["schedule_time"]:
                    schedule_time = dt_time.fromisoformat(sched["schedule_time"])
                    time_display = schedule_time.strftime("%I:%M %p").lstrip("0")
                    messages.append(f"• Daily weather at {time_display} (next: {date_str} at {time_str})")
                else:
                    messages.append(f"• Daily weather (next: {date_str} at {time_str})")
            else:
                messages.append(f"• One-time weather (next: {date_str} at {time_str})")
        
        response = "Your scheduled messages:\n" + "\n".join(messages)
        applescript_helpers.send_imessage(msg.handle_id, response)
        return
    
    # Unknown message - send friendly response with weather
    p = database.get_person(msg.handle_id)
    loc = p.get("location_text")
    lat = p.get("lat")
    lon = p.get("lon")
    first_name = display_first_name(msg.handle_id)
    
    if loc and lat is not None and lon is not None:
        # Get weather for friendly response
        try:
            parts = [p.strip() for p in loc.split(",")]
            city = parts[0] if parts else loc
            state = parts[1] if len(parts) > 1 and len(parts[1]) == 2 else None
            forecast = weather.wttr_forecast(city, state, "US")
            city_name = extract_city_name(loc)
            response = f"Hi {first_name}! Looks like the weather forecast is {forecast} for {city_name}. Hope all is well with you!"
        except Exception:
            city_name = extract_city_name(loc)
            response = f"Hi {first_name}! Hope all is well with you!"
    else:
        response = f"Hi {first_name}! Hope all is well with you!"
    
    applescript_helpers.send_imessage(msg.handle_id, response)
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

