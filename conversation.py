#!/usr/bin/env python3
"""
Conversation state machine and message handling logic.
"""
import re
from datetime import datetime, timezone
from typing import Optional

import applescript_helpers
import config
import database
import geocode
import message_polling
import scheduler
import weather
import aviation_weather
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

HELP_TEXT = """Hi! I'm Claude Puhfph, your weather assistant. You can ask me things naturally:

Weather questions:
• "What's the weather?" or "How's the weather?"
• "Tell me the weather" or "Give me a forecast"
• "What's it like outside?" or "How's it outside?"
• Or just say "weather" or "wx"

Location updates:
• "I'm in Davis, CA now"
• "I'm in Seattle, WA"
• Or just say "Davis, CA"

Scheduling:
• "Send me the weather at 7am everyday"
• "Text me the weather in 5 mins"
• "Schedule weather at 7:30pm daily"

Other questions:
• "What do I have scheduled?" or "Show my schedule"
• "When did we last talk?" or "Last contact"
• "Set an alarm to wake up" or "Remind me to call mom"

Aviation (METAR) weather:
• "aviation kdwa" or "metar kdwa"
• "airport wx kedu,kpao"
• "avnwx kpao"

Feel free to ask naturally - I understand conversational language!
"""

# Natural language keywords for intent detection
WEATHER_KEYWORDS = {"weather", "forecast", "temperature", "temp", "wx", "rain", "sunny", "cloudy", "snow", "wind", "outside", "conditions"}
WEATHER_QUESTIONS = {"what's the weather", "how's the weather", "what is the weather", "tell me the weather", 
                     "give me the weather", "show me the weather", "weather forecast", "weather report",
                     "how is it outside", "what's it like outside", "how's it outside", "what's the weather like"}

AVIATION_KEYWORDS = {
    "aviation", "metar", "airport", "airport wx", "airport weather", "avn", "avnwx", "avn wx"
}

LAST_CONTACT_KEYWORDS = {"last", "contact", "talk", "spoke", "messaged", "texted", "when did", "how long"}
LAST_CONTACT_QUESTIONS = {"when did we last talk", "when did we last contact", "when did we last speak",
                          "how long ago did we talk", "last time we talked", "last contact"}

SCHEDULE_KEYWORDS = {"schedule", "scheduled", "scheduling", "reminder", "remind"}
SCHEDULE_QUERY_KEYWORDS = {"what", "show", "list", "tell", "my", "have"}
SCHEDULE_QUERY_QUESTIONS = {"what do i have scheduled", "what's scheduled", "show my schedule", 
                            "list my schedule", "what schedules", "my schedules", "what reminders",
                            "show my reminders", "what do i have scheduled"}

# Alarm/reminder keywords
ALARM_KEYWORDS = {"alarm", "remind", "reminder", "alert", "wake"}
ALARM_PATTERNS = ["set an alarm", "set alarm", "create alarm", "set a reminder", "remind me", 
                  "create reminder", "set reminder", "alarm for", "reminder for"]

# Name change keywords
NAME_CHANGE_KEYWORDS = {"change", "update", "wrong", "correct", "fix", "my name is", "name should be"}
NAME_CHANGE_PATTERNS = ["change my name", "update my name", "my name is wrong", "fix my name", 
                        "correct my name", "my name should be", "update name"]

IN_NOW_RE = re.compile(
    r"""^\s*(?:i'?m|im|i\s+am)\s+in\s+(?P<loc>.+?)(?:\s+now)?\s*[.!?]?\s*$""",
    re.IGNORECASE,
)


def normalize_text(s: str) -> str:
    """Normalize whitespace in text."""
    return geocode.normalize_text(s)


def is_help(text: str) -> bool:
    """Check if text is a help command."""
    t = normalize_text(text).lower()
    help_keywords = {"help", "?", "commands", "what can you do", "what do you do", "show help", "help me"}
    return t in help_keywords or any(kw in t for kw in ["help", "what can", "what do you"])


def is_yes(text: str) -> bool:
    """Check if text is a yes/affirmative response."""
    t = normalize_text(text).lower()
    return t in {"yes", "y", "sure", "ok", "okay", "yeah", "yep", "please"}


def is_no(text: str) -> bool:
    """Check if text is a no/negative response."""
    t = normalize_text(text).lower()
    return t in {"no", "n", "nope", "nah"}


def is_aviation_cmd(text: str) -> bool:
    """Check if text is requesting METAR/aviation weather."""
    t = normalize_text(text).lower()
    if any(kw in t for kw in AVIATION_KEYWORDS):
        return True
    # If the message is just a list of station IDs, treat as aviation request
    if re.fullmatch(r"[a-zA-Z,\s]+", t or "") and re.search(r"\b[a-zA-Z]{4}\b", t):
        return True
    return False


def extract_station_ids(text: str) -> list[str]:
    """Extract station IDs (4-letter) from text."""
    t = normalize_text(text).lower()
    matches = re.findall(r"\b[a-z]{4}\b", t)
    return [m.upper() for m in matches]


def is_weather_cmd(text: str) -> bool:
    """Check if text is asking about weather using natural language."""
    t = normalize_text(text).lower()
    
    # Check for exact weather questions
    if any(q in t for q in WEATHER_QUESTIONS):
        return True
    
    # Check for weather keywords combined with question words
    has_weather_keyword = any(kw in t for kw in WEATHER_KEYWORDS)
    has_question_word = any(qw in t for qw in ["what", "how", "tell", "give", "show", "what's", "what is"])
    
    # Simple weather commands (exact matches)
    if t in {"weather", "wx", "forecast", "temp"}:
        return True
    
    # Natural language: "what's the weather" or "how's the weather" or "tell me about the weather"
    if has_weather_keyword and (has_question_word or t.startswith(("what", "how", "tell", "give", "show"))):
        return True
    
    return False


def is_last_contact_cmd(text: str) -> bool:
    """Check if text is asking for last contact info using natural language."""
    t = normalize_text(text).lower()
    
    # Check for exact questions
    if any(q in t for q in LAST_CONTACT_QUESTIONS):
        return True
    
    # Check for keywords that suggest asking about last contact
    has_contact_keyword = any(kw in t for kw in LAST_CONTACT_KEYWORDS)
    has_question_word = any(qw in t for qw in ["when", "how long", "what time", "last"])
    
    # Natural language patterns
    if has_contact_keyword and has_question_word:
        return True
    
    # Patterns like "when did we last talk" or "how long ago"
    if ("when" in t or "how long" in t) and any(kw in t for kw in ["last", "talk", "contact", "speak"]):
        return True
    
    return False


def is_schedule_query_cmd(text: str) -> bool:
    """Check if text is asking about scheduled messages using natural language."""
    t = normalize_text(text).lower()
    
    # Check for exact questions
    if any(q in t for q in SCHEDULE_QUERY_QUESTIONS):
        return True
    
    # Check for schedule keywords combined with query keywords
    has_schedule_keyword = any(kw in t for kw in SCHEDULE_KEYWORDS)
    has_query_keyword = any(kw in t for kw in SCHEDULE_QUERY_KEYWORDS)
    
    # Natural language patterns
    if has_schedule_keyword and has_query_keyword:
        return True
    
    # Patterns like "what do i have scheduled" or "show my reminders"
    if ("what" in t or "show" in t or "list" in t) and ("schedule" in t or "reminder" in t):
        return True
    
    return False


def is_name_change_cmd(text: str) -> bool:
    """Check if text is requesting a name change."""
    t = normalize_text(text).lower()
    
    # Check for exact patterns
    if any(pattern in t for pattern in NAME_CHANGE_PATTERNS):
        return True
    
    # Check for keywords that suggest name change
    has_name_keyword = "name" in t
    has_change_keyword = any(kw in t for kw in NAME_CHANGE_KEYWORDS)
    
    if has_name_keyword and has_change_keyword:
        return True
    
    return False


def extract_name_from_text(text: str) -> tuple[Optional[str], Optional[str]]:
    """Extract first and last name from text like 'my name is John Doe' or 'John Doe'."""
    t = normalize_text(text).lower()
    
    # Words that indicate this is just a request, not a name
    command_words = {"change", "update", "fix", "correct", "wrong", "should", "want", "to", "my", "name", "is", "i", "am"}
    
    # Remove common prefixes
    prefixes = ["my name is", "i'm", "i am", "call me", "name is", "it's", "it is", "update my name to", "change my name to", "i want to change my name", "i want to update my name"]
    for prefix in prefixes:
        if t.startswith(prefix):
            t = t[len(prefix):].strip()
            break
    
    # If after removing prefix, we only have command words, no name was provided
    parts = t.split()
    if not parts:
        return None, None
    
    # Filter out command words
    name_parts = [p for p in parts if p not in command_words]
    if not name_parts:
        return None, None
    
    # If we only have single-letter words or pronouns, it's not a name
    if all(len(p) <= 1 or p in {"i", "me", "you", "he", "she", "it", "we", "they"} for p in name_parts):
        return None, None
    
    if len(name_parts) == 1:
        return name_parts[0].title(), None
    else:
        return name_parts[0].title(), " ".join(name_parts[1:]).title()


def extract_in_now_location(text: str) -> Optional[str]:
    """Extract location from "I'm in <place> now" pattern."""
    if not text:
        return None
    normalized = normalize_text(text)
    # Normalize common smart apostrophes to ASCII for regex matching.
    normalized = normalized.replace("\u2019", "'").replace("\u2018", "'")
    m = IN_NOW_RE.match(normalized)
    if not m:
        return None
    loc = normalize_text(m.group("loc"))
    return loc if loc else None


def is_alarm_cmd(text: str) -> bool:
    """Check if text is requesting to set an alarm or reminder."""
    t = normalize_text(text).lower()
    
    # Check for exact patterns
    if any(pattern in t for pattern in ALARM_PATTERNS):
        return True
    
    # Check for keywords that suggest alarm/reminder
    has_alarm_keyword = any(kw in t for kw in ALARM_KEYWORDS)
    has_action_keyword = any(kw in t for kw in ["set", "create", "make", "add"])
    
    if has_alarm_keyword and has_action_keyword:
        return True
    
    return False


def extract_alarm_title(text: str) -> Optional[str]:
    """Extract alarm/reminder title from text like 'set an alarm to wake up' or 'remind me to call mom'."""
    t = normalize_text(text).lower()
    
    # Remove common prefixes
    prefixes = ["set an alarm to", "set alarm to", "create alarm to", "set a reminder to", 
                "remind me to", "create reminder to", "set reminder to", "alarm for", "reminder for",
                "set an alarm", "set alarm", "create alarm", "set a reminder", "remind me",
                "create reminder", "set reminder"]
    
    for prefix in sorted(prefixes, key=len, reverse=True):  # Sort by length, longest first
        if prefix in t:
            # Extract everything after the prefix
            idx = t.find(prefix)
            remaining = t[idx + len(prefix):].strip()
            # Remove trailing "at" or "for" if present
            remaining = remaining.lstrip("at for").strip()
            
            # Check if remaining looks like a time (e.g., "2pm", "14:00", "at 2pm")
            # If so, don't use it as a title
            import re
            if re.match(r'^at\s+\d+|^\d+.*(pm|am|:\d{2})', remaining):
                return None
            
            if remaining:
                return remaining.title()
    
    return None


def extract_time_from_text(text: str) -> Optional[str]:
    """Extract time string from text like 'remind me at 2pm' or 'at 14:00'."""
    import re
    t = normalize_text(text).lower()
    
    # Pattern: "at 2pm", "at 14:00", "at 7:30pm", etc.
    # Look for "at" followed by time patterns
    patterns = [
        r'at\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm))',  # "at 2pm", "at 7:30pm"
        r'at\s+(\d{1,2}:\d{2})',  # "at 14:00", "at 7:30"
        r'(\d{1,2}(?::\d{2})?\s*(?:am|pm))',  # Just "2pm", "7:30pm" (if no "at")
        r'(\d{1,2}:\d{2})',  # Just "14:00" (if no "at")
    ]
    
    for pattern in patterns:
        match = re.search(pattern, t)
        if match:
            return match.group(1).strip()
    
    return None


def extract_weather_for_location(text: str) -> Optional[str]:
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


def get_last_contact_info(handle_id: str) -> Optional[tuple[int, str]]:
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

    # Check if user is responding to "Do you want help?" prompt
    temp_data = database.get_temp_data(msg.handle_id)
    if temp_data.get("awaiting_help_confirm"):
        if is_yes(msg.text):
            temp_data["awaiting_help_confirm"] = False
            database.set_temp_data(msg.handle_id, temp_data)
            applescript_helpers.send_imessage(msg.handle_id, HELP_TEXT)
            return
        if is_no(msg.text):
            temp_data["awaiting_help_confirm"] = False
            database.set_temp_data(msg.handle_id, temp_data)
            applescript_helpers.send_imessage(msg.handle_id, "Okay — let me know if you need anything.")
            return
        # Not a yes/no; clear the flag and continue normal handling
        temp_data["awaiting_help_confirm"] = False
        database.set_temp_data(msg.handle_id, temp_data)

    if is_help(msg.text):
        applescript_helpers.send_imessage(msg.handle_id, HELP_TEXT)
        return

    if is_aviation_cmd(msg.text):
        station_ids = extract_station_ids(msg.text)
        if not station_ids:
            applescript_helpers.send_imessage(
                msg.handle_id,
                "I couldn't find any 4-letter station IDs. Try: \"metar kdwa\" or \"airport wx kedu,kpao\".",
            )
            return
        try:
            lines = aviation_weather.fetch_metars(station_ids)
        except Exception as e:
            applescript_helpers.send_imessage(msg.handle_id, f"Aviation weather lookup failed: {e}")
            return
        if not lines:
            applescript_helpers.send_imessage(msg.handle_id, "No METAR data returned.")
            return
        reply = "AirPuff Weather:\n" + "\n".join(lines)
        applescript_helpers.send_imessage(msg.handle_id, reply)
        return
    
    # Check for name change request (works in any state)
    if is_name_change_cmd(msg.text):
        first, last = extract_name_from_text(msg.text)
        if first:
            database.update_person(msg.handle_id, first_name=first)
            if last:
                database.update_person(msg.handle_id, last_name=last)
            first_display = display_first_name(msg.handle_id)
            applescript_helpers.send_imessage(msg.handle_id, f"Got it! I've updated your name to {first_display}. What else can I help you with?")
        else:
            applescript_helpers.send_imessage(msg.handle_id, "I'd be happy to update your name! What should I call you? For example, you could say \"My name is John\" or \"John Doe\".")
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
            applescript_helpers.send_imessage(msg.handle_id, f"Hi {first}! Nice to meet you! What city are you in? For example, you could say \"Davis, CA\" or \"I'm in Seattle, WA\".")
            return

        applescript_helpers.send_imessage(msg.handle_id, "Hi there! I'm Claude Puhfph, your weather assistant. What's your first name?")
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
            applescript_helpers.send_imessage(msg.handle_id, f"Thanks {first}! What city are you in? You can say something like \"Davis, CA\" or \"I'm in Seattle, WA\".")
            return

    if state == "need_location":
        # Try to extract location from natural language
        loc = extract_in_now_location(msg.text) or normalize_text(msg.text)
        try:
            _, _, pretty = set_location(msg.handle_id, loc)
        except Exception as e:
            first = display_first_name(msg.handle_id)
            applescript_helpers.send_imessage(msg.handle_id, f"Sorry {first}, I couldn't find that location. Could you try again? For example: \"Davis, CA\" or \"I'm in Seattle, WA\".")
            return

        first = display_first_name(msg.handle_id)
        city_name = extract_city_name(pretty)
        applescript_helpers.send_imessage(msg.handle_id, f"Perfect! I've saved your location as {city_name}. You can ask me things like \"What's the weather?\" or \"How's the weather?\" anytime!")
        return

    # Check for alarm creation states
    if state == "need_alarm_time":
        # User is providing the alarm time
        temp_data = database.get_temp_data(msg.handle_id)
        alarm_title = temp_data.get("alarm_title", "Alarm")
        is_reminder = temp_data.get("is_reminder", False)
        
        # Try to parse time directly from message using parse_time
        alert_time, tz_str = scheduler.parse_time(msg.text)
        if alert_time:
            alert_time_str = alert_time.strftime("%H:%M:%S")
            temp_data["alert_time"] = alert_time_str
            temp_data["timezone"] = tz_str  # Store timezone if provided
            database.set_temp_data(msg.handle_id, temp_data)
            database.set_state(msg.handle_id, "need_alarm_message")
            first = display_first_name(msg.handle_id)
            alarm_type = "REMINDER" if is_reminder else "ALARM"
            applescript_helpers.send_imessage(msg.handle_id, f"Great! What message should I send for this {alarm_type.lower()}?")
            return
        else:
            first = display_first_name(msg.handle_id)
            applescript_helpers.send_imessage(msg.handle_id, f"Sorry {first}, I couldn't understand that time. Could you try again? For example: \"7am\", \"7:30pm\", or \"19:00\"")
            return
    
    if state == "need_alarm_message":
        # User is providing the alarm message
        temp_data = database.get_temp_data(msg.handle_id)
        alarm_title = temp_data.get("alarm_title", "Alarm")
        alert_time_str = temp_data.get("alert_time")
        is_reminder = temp_data.get("is_reminder", False)
        
        alert_message = normalize_text(msg.text)
        temp_data["alert_message"] = alert_message
        database.set_temp_data(msg.handle_id, temp_data)
        database.set_state(msg.handle_id, "need_alarm_repeat")
        first = display_first_name(msg.handle_id)
        alarm_type = "REMINDER" if is_reminder else "ALARM"
        applescript_helpers.send_imessage(msg.handle_id, f"Perfect! Should this {alarm_type.lower()} repeat daily? (yes/no)")
        return
    
    if state == "need_alarm_repeat":
        # User is answering if alarm should repeat daily
        temp_data = database.get_temp_data(msg.handle_id)
        alarm_title = temp_data.get("alarm_title", "Alarm")
        alert_time_str = temp_data.get("alert_time")
        alert_message = temp_data.get("alert_message", "")
        is_reminder = temp_data.get("is_reminder", False)
        
        # Parse yes/no answer
        t = normalize_text(msg.text).lower()
        repeat_daily = t in {"yes", "y", "yeah", "yep", "sure", "daily", "everyday", "every day"}
        
        schedule_type = scheduler.SCHEDULE_DAILY if repeat_daily else scheduler.SCHEDULE_ONCE
        
        # Parse alert time and calculate next_run_at
        alert_time = dt_time.fromisoformat(alert_time_str)
        tz_str = temp_data.get("timezone")  # Get timezone if it was provided
        next_run = scheduler.calculate_next_run(alert_time, schedule_type, tz_str=tz_str)
        
        # Create alarm
        alarm_id = database.create_alarm(
            msg.handle_id,
            alarm_title,
            alert_time_str,
            alert_message,
            schedule_type,
            next_run.isoformat()
        )
        
        # Clear temp data and reset state
        database.set_temp_data(msg.handle_id, {})
        database.set_state(msg.handle_id, "ready")
        
        # Send confirmation
        first = display_first_name(msg.handle_id)
        alarm_type = "REMINDER" if is_reminder else "ALARM"
        time_str = alert_time.strftime("%I:%M %p").lstrip("0")
        repeat_str = "daily" if repeat_daily else "once"
        applescript_helpers.send_imessage(msg.handle_id, f"Got it {first}! I've set your {alarm_type.lower()} \"{alarm_title}\" for {time_str} ({repeat_str}).")
        return

    # ready state:
    # Check for alarm creation command
    if is_alarm_cmd(msg.text):
        is_reminder = "remind" in normalize_text(msg.text).lower()
        
        # Try to extract time string from the command first (e.g., "remind me at 2pm")
        time_str = extract_time_from_text(msg.text)
        
        # Extract title, but remove time-related parts
        alarm_title = extract_alarm_title(msg.text)
        
        # If we found a time string in the command, parse it
        if time_str:
            alert_time, tz_str = scheduler.parse_time(time_str)
            if alert_time:
                alert_time_str = alert_time.strftime("%H:%M:%S")
                # Use extracted title or default
                if not alarm_title:
                    alarm_title = "Reminder" if is_reminder else "Alarm"
                
                temp_data = {
                    "alarm_title": alarm_title,
                    "alert_time": alert_time_str,
                    "is_reminder": is_reminder,
                    "timezone": tz_str
                }
                database.set_temp_data(msg.handle_id, temp_data)
                database.set_state(msg.handle_id, "need_alarm_message")
                first = display_first_name(msg.handle_id)
                alarm_type = "REMINDER" if is_reminder else "ALARM"
                applescript_helpers.send_imessage(msg.handle_id, f"Great! What message should I send for this {alarm_type.lower()}?")
                return
        
        # No time found - ask for time first
        if alarm_title:
            # Store alarm title and type in temp_data
            temp_data = {"alarm_title": alarm_title, "is_reminder": is_reminder}
            database.set_temp_data(msg.handle_id, temp_data)
            database.set_state(msg.handle_id, "need_alarm_time")
            first = display_first_name(msg.handle_id)
            alarm_type = "REMINDER" if is_reminder else "ALARM"
            applescript_helpers.send_imessage(msg.handle_id, f"Great! What time should I set this {alarm_type.lower()} for? For example: \"7am\", \"7:30pm\", or \"19:00\"")
            return
        else:
            first = display_first_name(msg.handle_id)
            applescript_helpers.send_imessage(msg.handle_id, f"I'd be happy to set an alarm or reminder for you, {first}! What should I call it? For example: \"set an alarm to wake up\" or \"remind me to call mom\"")
            return
    
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
            first = display_first_name(msg.handle_id)
            applescript_helpers.send_imessage(msg.handle_id, f"I'd love to give you the weather, {first}! What city are you in? You can say something like \"Davis, CA\" or \"I'm in Seattle, WA\".")
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
            first = display_first_name(msg.handle_id)
            applescript_helpers.send_imessage(msg.handle_id, f"I'd love to help with that, {first}! First, what city are you in? You can say something like \"Davis, CA\" or \"I'm in Seattle, WA\".")
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
    
    # Unknown message - send friendly response with weather and offer help
    p = database.get_person(msg.handle_id)
    loc = p.get("location_text")
    lat = p.get("lat")
    lon = p.get("lon")
    first_name = display_first_name(msg.handle_id)
    
    response_parts = [f"Hi {first_name}! I'm not sure I understand. For help, just ask me for 'Help'."]
    
    if loc and lat is not None and lon is not None:
        # Get weather for friendly response
        try:
            parts = [p.strip() for p in loc.split(",")]
            city = parts[0] if parts else loc
            state = parts[1] if len(parts) > 1 and len(parts[1]) == 2 else None
            forecast = weather.wttr_forecast(city, state, "US")
            city_name = extract_city_name(loc)
            response_parts.append(f"It looks like the weather forecast is {forecast} for {city_name}.")
        except Exception:
            pass
    
    response_parts.append("Do you want me to show the help info?")
    response = " ".join(response_parts)
    
    applescript_helpers.send_imessage(msg.handle_id, response)
    
    # Remember that we asked about help, so a "yes" can trigger it
    temp_data = database.get_temp_data(msg.handle_id)
    temp_data["awaiting_help_confirm"] = True
    database.set_temp_data(msg.handle_id, temp_data)
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


def execute_alarm(alarm_data: dict) -> None:
    """Execute an alarm/reminder and send formatted message."""
    handle_id = alarm_data["handle_id"]
    alarm_title = alarm_data["alarm_title"]
    alert_time_str = alarm_data["alert_time"]
    alert_message = alarm_data["alert_message"]
    schedule_type = alarm_data["schedule_type"]
    
    # Determine if it's an alarm or reminder based on title/keywords
    is_reminder = "remind" in alarm_title.lower() or "reminder" in alarm_title.lower()
    alarm_type = "REMINDER" if is_reminder else "ALARM"
    
    # Format time
    alert_time = dt_time.fromisoformat(alert_time_str)
    time_str = alert_time.strftime("%I:%M %p").lstrip("0")
    
    # Get current time for "Sent @ HH:MM - m/d/y"
    now = datetime.now(timezone.utc).astimezone()
    sent_time = now.strftime("%H:%M")
    # Format date as m/d/y (remove leading zeros)
    month = str(now.month)
    day = str(now.day)
    year = now.strftime("%y")
    sent_date = f"{month}/{day}/{year}"
    
    # Format message as specified
    message = f"{alarm_type}: {alarm_title}\nTime: {time_str}\nMessage: {alert_message}\n\nSent @ {sent_time} - {sent_date}"
    
    applescript_helpers.send_imessage(handle_id, message)
    
    # Update next run time or delete if one-time
    if schedule_type == scheduler.SCHEDULE_ONCE:
        database.delete_alarm(alarm_data["alarm_id"])
    else:
        # Calculate next run for daily alarms
        next_run = scheduler.calculate_next_run(alert_time, schedule_type, tz_str=None)
        database.update_alarm_next_run(alarm_data["alarm_id"], next_run.isoformat())

