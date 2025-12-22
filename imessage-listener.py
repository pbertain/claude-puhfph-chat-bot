#!/usr/bin/env python3
import pathlib
import sqlite3
import subprocess
import sys
import time
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests

# ------------ paths / config ------------

STATE_FILE = pathlib.Path.home() / ".imessage_autoreply_last_rowid"
CHAT_DB = pathlib.Path.home() / "Library/Messages/chat.db"

PROFILE_DB = pathlib.Path.home() / ".imessage_autoreply_profiles.sqlite3"

POLL_SECONDS = 3

# NWS asks for a descriptive UA with contact info
NWS_USER_AGENT = "imessage-autoreply-bot/1.1 (claudep; contact: you@example.com)"

# ------------ AppleScript helpers ------------

SEND_SCRIPT = r'''
on run argv
  if (count of argv) < 2 then return "ERR:ARGS"
  set targetHandle to item 1 of argv
  set replyText to item 2 of argv

  tell application "Messages"
    set targetService to first service whose service type = iMessage
    set theBuddy to buddy targetHandle of targetService
    send replyText to theBuddy
    return "OK"
  end tell
end run
'''

CONTACT_NAME_SCRIPT = r'''
on run argv
  if (count of argv) < 1 then return ""
  set h to item 1 of argv

  tell application "Contacts"
    set people to every person whose (value of every email contains h)
    if (count of people) > 0 then
      set p to item 1 of people
      set fn to first name of p
      set ln to last name of p
      if fn is not missing value then
        if ln is not missing value then return (fn & " " & ln)
        return fn
      end if
    end if

    set people2 to every person whose (value of every phone contains h)
    if (count of people2) > 0 then
      set p2 to item 1 of people2
      set fn2 to first name of p2
      set ln2 to last name of p2
      if fn2 is not missing value then
        if ln2 is not missing value then return (fn2 & " " & ln2)
        return fn2
      end if
    end if
  end tell

  return ""
end run
'''

def run_osascript(script: str, args: list[str]) -> str:
    p = subprocess.run(
        ["/usr/bin/osascript", "-l", "AppleScript", "-e", script, *args],
        text=True,
        capture_output=True,
    )
    if p.returncode != 0:
        raise RuntimeError((p.stderr or "").strip() or "osascript failed")
    return (p.stdout or "").strip()

def send_imessage(handle_id: str, text: str) -> None:
    res = run_osascript(SEND_SCRIPT, [handle_id, text])
    if res != "OK":
        raise RuntimeError(f"Messages send failed: {res}")

def lookup_contact_name(handle_id: str) -> str:
    try:
        return run_osascript(CONTACT_NAME_SCRIPT, [handle_id]).strip()
    except Exception:
        return ""

# ------------ state file ------------

def read_last_rowid() -> int:
    try:
        return int(STATE_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return 0

def write_last_rowid(rowid: int) -> None:
    STATE_FILE.write_text(str(rowid), encoding="utf-8")

# ------------ iMessage DB polling ------------

@dataclass
class Incoming:
    rowid: int
    handle_id: str
    text: str

def get_latest_incoming_since(last_rowid: int) -> Optional[Incoming]:
    if not CHAT_DB.exists():
        raise FileNotFoundError(f"Missing Messages DB: {CHAT_DB}")

    uri = f"file:{CHAT_DB}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row

    row = con.execute(
        """
        SELECT
            message.ROWID AS rowid,
            handle.id AS handle_id,
            COALESCE(message.text, '') AS text
        FROM message
        JOIN handle ON handle.ROWID = message.handle_id
        WHERE message.is_from_me = 0
          AND message.ROWID > ?
        ORDER BY message.date DESC
        LIMIT 1
        """,
        (last_rowid,),
    ).fetchone()
    con.close()

    if row is None:
        return None

    return Incoming(
        rowid=int(row["rowid"]),
        handle_id=str(row["handle_id"]),
        text=str(row["text"] or "").strip(),
    )

# ------------ your profile DB + conversation state ------------

def db_connect() -> sqlite3.Connection:
    con = sqlite3.connect(PROFILE_DB)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA foreign_keys=ON;")
    return con

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def db_init() -> None:
    con = db_connect()
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS person (
          handle_id TEXT PRIMARY KEY,
          full_name TEXT,
          location_text TEXT,
          lat REAL,
          lon REAL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS convo_state (
          handle_id TEXT PRIMARY KEY,
          state TEXT NOT NULL,            -- 'need_name' | 'need_name_waiting' | 'need_location' | 'ready'
          updated_at TEXT NOT NULL,
          FOREIGN KEY(handle_id) REFERENCES person(handle_id) ON DELETE CASCADE
        );
        """
    )
    con.commit()
    con.close()

def ensure_person_row(handle_id: str) -> None:
    con = db_connect()
    ts = now_iso()
    con.execute(
        """
        INSERT INTO person(handle_id, created_at, updated_at)
        VALUES(?, ?, ?)
        ON CONFLICT(handle_id) DO NOTHING
        """,
        (handle_id, ts, ts),
    )
    con.execute(
        """
        INSERT INTO convo_state(handle_id, state, updated_at)
        VALUES(?, 'need_name', ?)
        ON CONFLICT(handle_id) DO NOTHING
        """,
        (handle_id, ts),
    )
    con.commit()
    con.close()

def get_state(handle_id: str) -> str:
    con = db_connect()
    row = con.execute(
        "SELECT state FROM convo_state WHERE handle_id = ?",
        (handle_id,),
    ).fetchone()
    con.close()
    return row[0] if row else "need_name"

def set_state(handle_id: str, state: str) -> None:
    con = db_connect()
    con.execute(
        "UPDATE convo_state SET state = ?, updated_at = ? WHERE handle_id = ?",
        (state, now_iso(), handle_id),
    )
    con.commit()
    con.close()

def update_person(handle_id: str, **fields) -> None:
    if not fields:
        return
    cols = []
    vals = []
    for k, v in fields.items():
        cols.append(f"{k} = ?")
        vals.append(v)
    cols.append("updated_at = ?")
    vals.append(now_iso())
    vals.append(handle_id)

    con = db_connect()
    con.execute(f"UPDATE person SET {', '.join(cols)} WHERE handle_id = ?", vals)
    con.commit()
    con.close()

def get_person(handle_id: str) -> dict:
    con = db_connect()
    row = con.execute(
        "SELECT handle_id, full_name, location_text, lat, lon FROM person WHERE handle_id = ?",
        (handle_id,),
    ).fetchone()
    con.close()
    if not row:
        return {}
    return {
        "handle_id": row[0],
        "full_name": row[1],
        "location_text": row[2],
        "lat": row[3],
        "lon": row[4],
    }

# ------------ Census geocode -> NWS forecast ------------

def census_geocode_city(city_state: str) -> tuple[float, float]:
    url = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
    params = {
        "address": city_state,
        "benchmark": "Public_AR_Current",
        "format": "json",
    }
    r = requests.get(url, params=params, timeout=8)
    r.raise_for_status()
    data = r.json()
    matches = (data.get("result", {}) or {}).get("addressMatches", []) or []
    if not matches:
        raise ValueError(f"No Census geocode match for: {city_state}")
    coords = matches[0].get("coordinates") or {}
    lon = float(coords["x"])
    lat = float(coords["y"])
    return lat, lon

def nws_forecast_one_liner(lat: float, lon: float) -> str:
    headers = {
        "User-Agent": NWS_USER_AGENT,
        "Accept": "application/geo+json, application/json",
    }
    points_url = f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}"
    p = requests.get(points_url, headers=headers, timeout=8)
    p.raise_for_status()
    pj = p.json()

    forecast_url = ((pj.get("properties") or {}).get("forecast"))
    if not forecast_url:
        raise ValueError("NWS points response missing properties.forecast")

    f = requests.get(forecast_url, headers=headers, timeout=8)
    f.raise_for_status()
    fj = f.json()

    periods = ((fj.get("properties") or {}).get("periods")) or []
    if not periods:
        raise ValueError("NWS forecast response missing periods")

    first = periods[0]
    name = first.get("name", "Forecast")
    temp = first.get("temperature")
    unit = first.get("temperatureUnit")
    short = first.get("shortForecast", "")
    return f"{name}: {temp}{unit}. {short}".strip()

# ------------ commands / parsing ------------

HELP_TEXT = """Commands:
• help / ?              Show this help
• weather / wx          Get your current forecast (based on saved location)
• I'm in <place> now    Update your location and get forecast
Examples:
• weather
• wx
• I'm in Davis, CA now
"""

WEATHER_COMMANDS = {"weather", "wx", "forecast", "temp"}

# Matches:
#   "I'm in Davis, CA now"
#   "I am in Davis, CA now"
#   "im in Davis, CA now"
#   "in Davis, CA now"
IN_NOW_RE = re.compile(
    r"""^\s*(?:i'?m|i\s+am)?\s*in\s+(?P<loc>.+?)\s+now\s*$""",
    re.IGNORECASE,
)

def normalize_text(s: str) -> str:
    return " ".join((s or "").strip().split())

def is_help(text: str) -> bool:
    t = normalize_text(text).lower()
    return t in {"help", "?", "commands"}

def is_weather_cmd(text: str) -> bool:
    t = normalize_text(text).lower()
    return t in WEATHER_COMMANDS

def extract_in_now_location(text: str) -> str | None:
    m = IN_NOW_RE.match(text or "")
    if not m:
        return None
    loc = normalize_text(m.group("loc"))
    return loc if loc else None

# ------------ conversation logic ------------

def set_location(handle_id: str, loc: str) -> tuple[float, float]:
    lat, lon = census_geocode_city(loc)
    update_person(handle_id, location_text=loc, lat=lat, lon=lon)
    set_state(handle_id, "ready")
    return lat, lon

def reply_weather(handle_id: str, display_name: str, loc: str, lat: float, lon: float) -> None:
    try:
        wx = nws_forecast_one_liner(lat, lon)
    except Exception as e:
        wx = f"Weather lookup failed ({e})"
    who = display_name or "there"
    send_imessage(handle_id, f"Hello {who}! Forecast for {loc}:\n\n{wx}")

def handle_incoming(msg: Incoming) -> None:
    ensure_person_row(msg.handle_id)

    # Basic ignore: empty messages
    if not msg.text:
        return

    # HELP works at any time/state
    if is_help(msg.text):
        send_imessage(msg.handle_id, HELP_TEXT)
        return

    # Handle "I'm in <location> now" at any time:
    in_now_loc = extract_in_now_location(msg.text)
    if in_now_loc:
        # Ensure we have a name if Contacts knows it; otherwise we can still proceed
        contact_name = lookup_contact_name(msg.handle_id)
        person = get_person(msg.handle_id)
        display_name = contact_name or person.get("full_name") or ""
        try:
            lat, lon = set_location(msg.handle_id, in_now_loc)
        except Exception as e:
            send_imessage(msg.handle_id, f"Sorry — I couldn’t find that location. Try: “Davis, CA”. ({e})")
            return
        reply_weather(msg.handle_id, display_name, in_now_loc, lat, lon)
        return

    state = get_state(msg.handle_id)
    person = get_person(msg.handle_id)

    contact_name = lookup_contact_name(msg.handle_id)
    display_name = contact_name or person.get("full_name") or ""

    # Onboarding flow (only for unknowns / incomplete profile)
    if state == "need_name":
        if contact_name:
            update_person(msg.handle_id, full_name=contact_name)
            set_state(msg.handle_id, "need_location")
            send_imessage(msg.handle_id, f"Hello {contact_name}! What city and state are you in? (e.g., Davis, CA)")
            return

        send_imessage(msg.handle_id, "Hi! What’s your full name?")
        set_state(msg.handle_id, "need_name_waiting")
        return

    if state == "need_name_waiting":
        name = normalize_text(msg.text)
        update_person(msg.handle_id, full_name=name)
        set_state(msg.handle_id, "need_location")
        send_imessage(msg.handle_id, f"Nice to meet you, {name}. What city and state are you in? (e.g., Davis, CA)")
        return

    if state == "need_location":
        loc = normalize_text(msg.text)
        try:
            lat, lon = set_location(msg.handle_id, loc)
        except Exception as e:
            send_imessage(msg.handle_id, f"Sorry — I couldn’t find that location. Try: “Davis, CA”. ({e})")
            return

        # Do NOT auto-send weather here unless they asked; just confirm setup.
        who = display_name or "there"
        send_imessage(msg.handle_id, f"Thanks {who}! Saved your location as: {loc}. Text “weather” or “wx” anytime.")
        return

    # state == ready:
    # Only reply with weather if they explicitly ask
    if is_weather_cmd(msg.text):
        loc = person.get("location_text")
        lat = person.get("lat")
        lon = person.get("lon")
        if not loc or lat is None or lon is None:
            set_state(msg.handle_id, "need_location")
            send_imessage(msg.handle_id, "What city and state are you in? (e.g., Davis, CA)")
            return
        reply_weather(msg.handle_id, display_name, loc, float(lat), float(lon))
        return

    # Otherwise: ignore (no auto-replies)
    return

# ------------ main loop ------------

def main() -> int:
    db_init()
    last_rowid = read_last_rowid()
    print("iMessage bot running. Ctrl-C to stop.")

    while True:
        try:
            inc = get_latest_incoming_since(last_rowid)
            if inc is None:
                time.sleep(POLL_SECONDS)
                continue

            # advance rowid immediately to avoid re-processing on crash
            last_rowid = inc.rowid
            write_last_rowid(last_rowid)

            handle_incoming(inc)
            time.sleep(POLL_SECONDS)

        except KeyboardInterrupt:
            print("\nbye")
            return 0
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            time.sleep(5)

if __name__ == "__main__":
    raise SystemExit(main())

