#!/usr/bin/env python3
"""
Scheduler for recurring messages (e.g., daily weather reports).
"""
import re
from datetime import datetime, time, timedelta, timezone
from typing import Optional
import pytz

import database


# Schedule patterns
SCHEDULE_DAILY = "daily"
SCHEDULE_ONCE = "once"

# METAR/aviation keywords for scheduling
METAR_KEYWORDS = {
    "metar", "aviation", "airport wx", "airport weather", "avnwx", "avn wx", "airport",
}


# Timezone abbreviations mapping
TZ_MAP = {
    "pt": "America/Los_Angeles",
    "pst": "America/Los_Angeles",
    "pdt": "America/Los_Angeles",
    "mt": "America/Denver",
    "mst": "America/Denver",
    "mdt": "America/Denver",
    "ct": "America/Chicago",
    "cst": "America/Chicago",
    "cdt": "America/Chicago",
    "et": "America/New_York",
    "est": "America/New_York",
    "edt": "America/New_York",
}


def parse_time(text: str, tz_str: Optional[str] = None) -> tuple[Optional[time], Optional[str]]:
    """
    Parse time from text like "7am", "7:30pm", "19:00", "7:30 AM PT".
    Returns (time object, timezone string) or (None, None) if parsing fails.
    """
    text = text.strip().lower()
    
    # Extract timezone if present
    tz_abbr = None
    for tz_key in TZ_MAP.keys():
        if text.endswith(f" {tz_key}"):
            tz_abbr = TZ_MAP[tz_key]
            text = text[:-len(f" {tz_key}")].strip()
            break
    
    # Use provided timezone or extracted one
    timezone_str = tz_str or tz_abbr
    
    # Handle 24-hour format: "19:00", "7:30", "00:10"
    # Also handle ambiguous times like "1:20" - if 1-7, assume PM; 8-11 assume AM
    if re.match(r'^\d{1,2}:\d{2}$', text):
        try:
            parts = text.split(':')
            hour = int(parts[0])
            minute = int(parts[1])
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                # If hour is 0 (midnight), treat as 24-hour (00:xx = 12:xx AM)
                if hour == 0:
                    return (time(0, minute), timezone_str)
                # If hour is 1-7 without am/pm, assume PM (common afternoon times)
                elif 1 <= hour <= 7:
                    return (time(hour + 12, minute), timezone_str)
                # If hour is 8-11, assume AM (common morning times)
                elif 8 <= hour <= 11:
                    return (time(hour, minute), timezone_str)
                # If hour is 12-23, treat as 24-hour
                else:
                    return (time(hour, minute), timezone_str)
        except ValueError:
            pass
    
    # Handle 12-hour format with am/pm: "7am", "7:30pm", "7:30 AM"
    am_pm_pattern = r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)'
    match = re.match(am_pm_pattern, text)
    if match:
        try:
            hour = int(match.group(1))
            minute = int(match.group(2) or "0")
            period = match.group(3).lower()
            
            if hour < 1 or hour > 12:
                return (None, None)
            if minute < 0 or minute > 59:
                return (None, None)
            
            # Convert to 24-hour
            if period == "pm" and hour != 12:
                hour += 12
            elif period == "am" and hour == 12:
                hour = 0
            
            return (time(hour, minute), timezone_str)
        except (ValueError, AttributeError):
            pass
    
    return (None, None)


def parse_relative_time(text: str) -> Optional[timedelta]:
    """
    Parse relative time like "in 5 mins", "in 2 hours", "in 30 minutes".
    Returns timedelta or None if parsing fails.
    """
    text = text.strip().lower()
    
    # Pattern: "in X (minute|min|mins|hour|hr|hrs|hour|hours)"
    patterns = [
        r'in\s+(\d+)\s+(minute|min|mins)\s*$',
        r'in\s+(\d+)\s+(hour|hr|hrs|hours)\s*$',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                amount = int(match.group(1))
                unit = match.group(2).lower()
                
                if unit in ("minute", "min", "mins"):
                    return timedelta(minutes=amount)
                elif unit in ("hour", "hr", "hrs", "hours"):
                    return timedelta(hours=amount)
            except (ValueError, IndexError):
                pass
    
    return None


def parse_schedule_command(text: str) -> Optional[dict]:
    """
    Parse a schedule command like:
    - "send me the weather at 7am everyday"
    - "text me the weather in 5 mins"
    - "send me the weather at 7:45am PT"
    
    Returns dict with 'time', 'schedule', 'message_type', 'timezone' or None if not a schedule command.
    """
    text_orig = text
    text = text.strip().lower()
    
    # Check for relative time first: "text me the weather in 5 mins"
    relative_patterns = [
        r'(?:text|send)\s+me\s+(?:the\s+)?weather\s+in\s+(\d+\s+(?:minute|min|mins|hour|hr|hrs|hours))',
        r'(?:text|send)\s+(?:me\s+)?(?:the\s+)?weather\s+in\s+(\d+\s+(?:minute|min|mins|hour|hr|hrs|hours))',
    ]
    
    for pattern in relative_patterns:
        match = re.search(pattern, text)
        if match:
            relative_str = match.group(1)
            delta = parse_relative_time(f"in {relative_str}")
            if delta:
                return {
                    "relative_delta": delta,
                    "schedule": SCHEDULE_ONCE,
                    "message_type": "weather",
                    "timezone": None,
                }
    
    # Pattern: "send me [something] at [time] [timezone] [frequency]"
    # Variations:
    # - "send me the weather at 7am everyday"
    # - "send me weather at 7:30pm PT daily"
    # - "send weather at 7:45am PST"
    # - "schedule weather at 7am everyday"
    
    patterns = [
        r'(?:text|send)\s+me\s+(?:the\s+)?weather\s+at\s+([\d:]+(?:\s*(?:am|pm))?(?:\s+(?:pt|pst|pdt|mt|mst|mdt|ct|cst|cdt|et|est|edt))?)\s*(everyday|daily|once)?',
        r'(?:text|send)\s+(?:me\s+)?(?:the\s+)?weather\s+at\s+([\d:]+(?:\s*(?:am|pm))?(?:\s+(?:pt|pst|pdt|mt|mst|mdt|ct|cst|cdt|et|est|edt))?)\s*(everyday|daily|once)?',
        r'schedule\s+(?:me\s+)?(?:the\s+)?weather\s+at\s+([\d:]+(?:\s*(?:am|pm))?(?:\s+(?:pt|pst|pdt|mt|mst|mdt|ct|cst|cdt|et|est|edt))?)\s*(everyday|daily|once)?',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            time_str = match.group(1).strip()
            frequency = (match.group(2) or "").strip().lower() if len(match.groups()) > 1 else ""
            
            parsed_time, tz = parse_time(time_str)
            if not parsed_time:
                return None
            
            # Determine schedule type
            if frequency in ("everyday", "daily", "every day"):
                schedule = SCHEDULE_DAILY
            elif frequency == "once":
                schedule = SCHEDULE_ONCE
            else:
                # Default to daily if no frequency specified
                schedule = SCHEDULE_DAILY
            
            return {
                "time": parsed_time,
                "schedule": schedule,
                "message_type": "weather",
                "timezone": tz,
            }
    
    return None


def parse_metar_schedule_command(text: str) -> Optional[dict]:
    """
    Parse a METAR scheduling command like:
    - "schedule metar at 7am daily"
    - "send aviation weather at 7:30pm"
    - "metar kdwa at 7am"
    - "aviation weather in 5 mins"
    """
    text_orig = text
    text = text.strip().lower()
    if not any(kw in text for kw in METAR_KEYWORDS):
        return None

    # Relative time: "in 5 mins"
    relative_match = re.search(r'in\s+(\d+\s+(?:minute|min|mins|hour|hr|hrs|hours))', text)
    if relative_match:
        delta = parse_relative_time(f"in {relative_match.group(1)}")
        if delta:
            return {
                "relative_delta": delta,
                "schedule": SCHEDULE_ONCE,
                "message_type": "metar",
                "timezone": None,
            }

    # Absolute time: "... at 7am [daily]"
    time_match = re.search(
        r'\bat\s+([\d:]+(?:\s*(?:am|pm))?(?:\s+(?:pt|pst|pdt|mt|mst|mdt|ct|cst|cdt|et|est|edt))?)',
        text,
    )
    if time_match:
        time_str = time_match.group(1).strip()
        parsed_time, tz = parse_time(time_str)
        if not parsed_time:
            return None

        # Determine schedule type
        if "everyday" in text or "daily" in text or "every day" in text:
            schedule = SCHEDULE_DAILY
        elif "once" in text:
            schedule = SCHEDULE_ONCE
        else:
            schedule = SCHEDULE_DAILY

        return {
            "time": parsed_time,
            "schedule": schedule,
            "message_type": "metar",
            "timezone": tz,
        }

    return None


def calculate_next_run(schedule_time: time, schedule_type: str, tz_str: Optional[str] = None, now: Optional[datetime] = None) -> datetime:
    """
    Calculate the next run time for a scheduled message.
    If tz_str is provided, interpret schedule_time in that timezone.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    
    if tz_str:
        # Use specified timezone
        tz = pytz.timezone(tz_str)
        tz_now = now.astimezone(tz)
        # Create naive datetime, then localize it
        scheduled_dt_naive = datetime.combine(tz_now.date(), schedule_time)
        scheduled_dt = tz.localize(scheduled_dt_naive)
        
        # If the time has already passed today, schedule for tomorrow
        if scheduled_dt <= tz_now:
            scheduled_dt += timedelta(days=1)
        
        # Convert back to UTC for storage
        return scheduled_dt.astimezone(timezone.utc)
    else:
        # Use local timezone
        local_now = now.astimezone()
        scheduled_dt_naive = datetime.combine(local_now.date(), schedule_time)
        # Make it timezone-aware by using replace (works for both timezone and pytz)
        scheduled_dt = scheduled_dt_naive.replace(tzinfo=local_now.tzinfo)
        
        # If the time has already passed today, schedule for tomorrow
        if scheduled_dt <= local_now:
            scheduled_dt += timedelta(days=1)
        
        # Convert back to UTC for storage
        return scheduled_dt.astimezone(timezone.utc)


def calculate_next_run_relative(delta: timedelta, now: Optional[datetime] = None) -> datetime:
    """Calculate next run time from a relative timedelta."""
    if now is None:
        now = datetime.now(timezone.utc)
    return now + delta


def add_scheduled_message(handle_id: str, message_type: str, schedule_time: Optional[time] = None,
                         schedule_type: str = SCHEDULE_ONCE, relative_delta: Optional[timedelta] = None,
                         tz_str: Optional[str] = None, message_payload: Optional[str] = None) -> int:
    """
    Add a scheduled message to the database.
    Either schedule_time (for absolute times) or relative_delta (for relative times) must be provided.
    Returns the schedule_id.
    """
    if relative_delta:
        next_run = calculate_next_run_relative(relative_delta)
        schedule_time_str = None  # Not applicable for relative times
    elif schedule_time:
        next_run = calculate_next_run(schedule_time, schedule_type, tz_str)
        schedule_time_str = schedule_time.strftime("%H:%M:%S")
    else:
        raise ValueError("Either schedule_time or relative_delta must be provided")
    
    def _do():
        con = database.db_connect()
        cursor = con.execute(
            """
            INSERT INTO scheduled_messages 
            (handle_id, message_type, message_payload, schedule_time, schedule_type, next_run_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                handle_id,
                message_type,
                message_payload,
                schedule_time_str,
                schedule_type,
                next_run.isoformat(),
                database.now_iso(),
                database.now_iso(),
            ),
        )
        schedule_id = cursor.lastrowid
        con.commit()
        con.close()
        return schedule_id
    
    return database.db_exec(_do)


def get_due_scheduled_messages(now: Optional[datetime] = None) -> list[dict]:
    """
    Get all scheduled messages that are due to run.
    Updates next_run_at immediately to prevent duplicate execution.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    
    def _do():
        con = database.db_connect()
        # Use a small buffer (1 second) to avoid immediate re-selection
        # Also update next_run_at to a far future time temporarily to mark as processing
        buffer_time = (now + timedelta(seconds=1)).isoformat()
        temp_marker = (now + timedelta(days=365)).isoformat()  # Far future marker
        
        rows = con.execute(
            """
            SELECT schedule_id, handle_id, message_type, message_payload, schedule_time, schedule_type, next_run_at
            FROM scheduled_messages
            WHERE next_run_at <= ?
            ORDER BY next_run_at ASC
            """,
            (buffer_time,),
        ).fetchall()
        
        # Immediately update next_run_at to prevent re-selection
        # We'll calculate the real next_run_at after execution
        schedule_ids = [row[0] for row in rows]
        if schedule_ids:
            placeholders = ','.join('?' * len(schedule_ids))
            con.execute(
                f"""
                UPDATE scheduled_messages
                SET next_run_at = ?, updated_at = ?
                WHERE schedule_id IN ({placeholders})
                """,
                (temp_marker, database.now_iso(), *schedule_ids),
            )
            con.commit()
        
        con.close()
        
        return [
            {
                "schedule_id": row[0],
                "handle_id": row[1],
                "message_type": row[2],
                "message_payload": row[3],
                "schedule_time": row[4],
                "schedule_type": row[5],
                "next_run_at": row[6],
            }
            for row in rows
        ]
    
    return database.db_exec(_do)


def update_next_run(schedule_id: int, schedule_time_str: Optional[str], schedule_type: str, tz_str: Optional[str] = None) -> None:
    """
    Update the next_run_at for a scheduled message after it has been executed.
    schedule_time_str should be in "HH:MM:SS" format, or None for relative time schedules.
    """
    now = datetime.now(timezone.utc)
    
    if schedule_type == SCHEDULE_ONCE:
        # Delete one-time schedules after execution
        delete_scheduled_message(schedule_id)
        return
    
    # If schedule_time_str is None, this is a relative time schedule that shouldn't recur
    # (but we already handled SCHEDULE_ONCE above, so this shouldn't happen)
    if schedule_time_str is None:
        delete_scheduled_message(schedule_id)
        return
    
    # Parse the time string back to time object
    schedule_time = time.fromisoformat(schedule_time_str)
    
    # Calculate next run for recurring schedules
    next_run = calculate_next_run(schedule_time, schedule_type, tz_str=tz_str, now=now)
    
    def _do():
        con = database.db_connect()
        con.execute(
            """
            UPDATE scheduled_messages
            SET next_run_at = ?, updated_at = ?
            WHERE schedule_id = ?
            """,
            (next_run.isoformat(), database.now_iso(), schedule_id),
        )
        con.commit()
        con.close()
    
    database.db_exec(_do)


def delete_scheduled_message(schedule_id: int) -> None:
    """Delete a scheduled message."""
    def _do():
        con = database.db_connect()
        con.execute("DELETE FROM scheduled_messages WHERE schedule_id = ?", (schedule_id,))
        con.commit()
        con.close()
    
    database.db_exec(_do)


def get_user_scheduled_messages(handle_id: str) -> list[dict]:
    """Get all scheduled messages for a user."""
    def _do():
        con = database.db_connect()
        rows = con.execute(
            """
            SELECT schedule_id, message_type, message_payload, schedule_time, schedule_type, next_run_at
            FROM scheduled_messages
            WHERE handle_id = ?
            ORDER BY next_run_at ASC
            """,
            (handle_id,),
        ).fetchall()
        con.close()
        
        return [
            {
                "schedule_id": row[0],
                "message_type": row[1],
                "message_payload": row[2],
                "schedule_time": row[3],
                "schedule_type": row[4],
                "next_run_at": row[5],
            }
            for row in rows
        ]
    
    return database.db_exec(_do)


def get_scheduled_messages_for_handle(handle_id: str) -> list[dict]:
    """Get all scheduled messages for a handle."""
    def _do():
        con = database.db_connect()
        rows = con.execute(
            """
            SELECT schedule_id, message_type, message_payload, schedule_time, schedule_type, next_run_at
            FROM scheduled_messages
            WHERE handle_id = ?
            ORDER BY next_run_at ASC
            """,
            (handle_id,),
        ).fetchall()
        con.close()
        
        return [
            {
                "schedule_id": row[0],
                "message_type": row[1],
                "message_payload": row[2],
                "schedule_time": row[3],
                "schedule_type": row[4],
                "next_run_at": row[5],
            }
            for row in rows
        ]
    
    return database.db_exec(_do)

