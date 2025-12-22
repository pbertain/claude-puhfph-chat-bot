#!/usr/bin/env python3
"""
Scheduler for recurring messages (e.g., daily weather reports).
"""
import re
from datetime import datetime, time, timedelta, timezone
from typing import Optional

import database


# Schedule patterns
SCHEDULE_DAILY = "daily"
SCHEDULE_ONCE = "once"


def parse_time(text: str) -> Optional[time]:
    """
    Parse time from text like "7am", "7:30pm", "19:00", "7:30 AM".
    Returns a time object or None if parsing fails.
    """
    text = text.strip().lower()
    
    # Handle 24-hour format: "19:00", "7:30"
    if re.match(r'^\d{1,2}:\d{2}$', text):
        try:
            parts = text.split(':')
            hour = int(parts[0])
            minute = int(parts[1])
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return time(hour, minute)
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
                return None
            if minute < 0 or minute > 59:
                return None
            
            # Convert to 24-hour
            if period == "pm" and hour != 12:
                hour += 12
            elif period == "am" and hour == 12:
                hour = 0
            
            return time(hour, minute)
        except (ValueError, AttributeError):
            pass
    
    return None


def parse_schedule_command(text: str) -> Optional[dict]:
    """
    Parse a schedule command like "send me the weather at 7am everyday".
    Returns dict with 'time', 'schedule', 'message_type' or None if not a schedule command.
    """
    text = text.strip().lower()
    
    # Pattern: "send me [something] at [time] [frequency]"
    # Variations:
    # - "send me the weather at 7am everyday"
    # - "send me weather at 7:30pm daily"
    # - "send weather at 7am"
    # - "schedule weather at 7am everyday"
    
    patterns = [
        r'send\s+me\s+(?:the\s+)?weather\s+at\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*(everyday|daily|once)?',
        r'send\s+(?:me\s+)?(?:the\s+)?weather\s+at\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*(everyday|daily|once)?',
        r'schedule\s+(?:me\s+)?(?:the\s+)?weather\s+at\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*(everyday|daily|once)?',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            time_str = match.group(1).strip()
            frequency = (match.group(2) or "").strip().lower() if match.groups() > 1 else ""
            
            parsed_time = parse_time(time_str)
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
            }
    
    return None


def calculate_next_run(schedule_time: time, schedule_type: str, now: Optional[datetime] = None) -> datetime:
    """
    Calculate the next run time for a scheduled message.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    
    # Convert schedule_time to datetime today in local timezone
    # We'll use UTC for storage but need to handle local time for scheduling
    local_now = now.astimezone()
    scheduled_dt = datetime.combine(local_now.date(), schedule_time)
    
    # If the time has already passed today, schedule for tomorrow
    if scheduled_dt <= local_now:
        scheduled_dt += timedelta(days=1)
    
    # Convert back to UTC for storage
    return scheduled_dt.astimezone(timezone.utc)


def add_scheduled_message(handle_id: str, message_type: str, schedule_time: time, schedule_type: str) -> int:
    """
    Add a scheduled message to the database.
    Returns the schedule_id.
    """
    next_run = calculate_next_run(schedule_time, schedule_type)
    
    def _do():
        con = database.db_connect()
        cursor = con.execute(
            """
            INSERT INTO scheduled_messages 
            (handle_id, message_type, schedule_time, schedule_type, next_run_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                handle_id,
                message_type,
                schedule_time.strftime("%H:%M:%S"),
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
    
    return database._db_exec(_do)


def get_due_scheduled_messages(now: Optional[datetime] = None) -> list[dict]:
    """
    Get all scheduled messages that are due to run.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    
    def _do():
        con = database.db_connect()
        rows = con.execute(
            """
            SELECT schedule_id, handle_id, message_type, schedule_time, schedule_type, next_run_at
            FROM scheduled_messages
            WHERE next_run_at <= ?
            ORDER BY next_run_at ASC
            """,
            (now.isoformat(),),
        ).fetchall()
        con.close()
        
        return [
            {
                "schedule_id": row[0],
                "handle_id": row[1],
                "message_type": row[2],
                "schedule_time": row[3],
                "schedule_type": row[4],
                "next_run_at": row[5],
            }
            for row in rows
        ]
    
    return database._db_exec(_do)


def update_next_run(schedule_id: int, schedule_time_str: str, schedule_type: str) -> None:
    """
    Update the next_run_at for a scheduled message after it has been executed.
    schedule_time_str should be in "HH:MM:SS" format.
    """
    now = datetime.now(timezone.utc)
    
    if schedule_type == SCHEDULE_ONCE:
        # Delete one-time schedules after execution
        delete_scheduled_message(schedule_id)
        return
    
    # Parse the time string back to time object
    schedule_time = time.fromisoformat(schedule_time_str)
    
    # Calculate next run for recurring schedules
    next_run = calculate_next_run(schedule_time, schedule_type, now)
    
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


def get_scheduled_messages_for_handle(handle_id: str) -> list[dict]:
    """Get all scheduled messages for a handle."""
    def _do():
        con = database.db_connect()
        rows = con.execute(
            """
            SELECT schedule_id, message_type, schedule_time, schedule_type, next_run_at
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
                "schedule_time": row[2],
                "schedule_type": row[3],
                "next_run_at": row[4],
            }
            for row in rows
        ]
    
    return database._db_exec(_do)

