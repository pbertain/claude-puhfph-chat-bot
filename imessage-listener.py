#!/usr/bin/env python3
import json
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

# NEW: geocoding helpers
from geocode_utils import AmbiguousLocation, GeocodeError, GeoCandidate, pick_best_or_raise, resolve_location_to_candidates

# ------------ paths / config ------------

STATE_FILE = pathlib.Path.home() / ".imessage_autoreply_last_rowid"
CHAT_DB = pathlib.Path.home() / "Library/Messages/chat.db"

PROFILE_DB = pathlib.Path.home() / ".imessage_autoreply_profiles.sqlite3"

POLL_SECONDS = 3

# NWS asks for a descriptive UA with contact info
NWS_USER_AGENT = "imessage-autoreply-bot/1.3 (claudep; contact: you@example.com)"

WELCOME_BACK_GAP_SECONDS = 15 * 60  # 15 minutes

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

# ------------ greeting ------------

def time_of_day_greeting(dt: datetime) -> str:
    h = dt.hour
    if 5 <= h < 12:
        return "Good morning!"
    if 12 <= h < 17:
        return "Good afternoon!"
    if 17 <= h < 24:
        return "Good evening!"
    return "Good god it's late!"

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

# ------------ profile DB ------------

def db_connect() -> sqlite3.Connection:
    con = sqlite3.connect(PROFILE_DB)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA foreign_keys=ON;")
    return con

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def parse_iso(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None

def db_init() -> None:
    con = db_connect()
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS person (
          handle_id TEXT PRIMARY KEY,
          first_name TEXT,
          last_name TEXT,
          location_text TEXT,
          lat REAL,
          lon REAL,
          first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS convo_state (
          handle_id TEXT PRIMARY KEY,
          state TEXT NOT NULL,              -- need_first | need_last | need_location | need_location_choice | ready
          last_incoming_at TEXT,
          last_welcome_at TEXT,

          -- NEW: pending disambiguation choices (JSON list of {label, lat, lon, source})
          pending_location_query TEXT,
          pending_location_choices TEXT,

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
        INSERT INTO person(handle_id, first_seen_at, last_seen_at, updated_at)
        VALUES(?, ?, ?, ?)
        ON CONFLICT(handle_id) DO NOTHING
        """,
        (handle_id, ts, ts, ts),
    )

    con.execute(
        """
        INSERT INTO convo_state(handle_id, state, last_incoming_at, last_welcome_at, pending_location_query, pending_location_choices, updated_at)
        VALUES(?, 'need_first', NULL, NULL, NULL, NULL, ?)
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
    return row[0] if row else "need_first"

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
        """
        SELECT handle_id, first_name, last_name, location_text, lat, lon,
               first_seen_at, last_seen_at
        FROM person WHERE handle_id = ?
        """,
        (handle_id,),
    ).fetchone()
    con.close()
    if not row:
        return {}
    return {
        "handle_id": row[0],
        "first_name": row[1],
        "last_name": row[2],
        "location_text": row[3],
        "lat": row[4],
        "lon": row[5],
        "first_seen_at": row[6],
        "last_seen_at": row[7],
    }

def get_convo_meta(handle_id: str) -> dict:
    con = db_connect()
    row = con.execute(
        """
        SELECT last_incoming_at, last_welcome_at,
               pending_location_query, pending_location_choices
        FROM convo_state WHERE handle_id = ?
        """,
        (handle_id,),
    ).fetchone()
    con.close()
    return {
        "last_incoming_at": row[0] if row else None,
        "last_welcome_at": row[1] if row else None,
        "pending_location_query": row[2] if row else None,
        "pending_location_choices": row[3] if row else None,
    }

def set_convo_meta(
    handle_id: str,
    *,
    last_incoming_at: str | None = None,
    last_welcome_at: str | None = None,
    pending_location_query: str | None = None,
    pending_location_choices: str | None = None,
) -> None:
    sets = []
    vals: list[str] = []
    if last_incoming_at is not None:
        sets.append("last_incoming_at = ?")
        vals.append(last_incoming_at)
    if last_welcome_at is not None:
        sets.append("last_welcome_at = ?")
        vals.append(last_welcome_at)
    if pending_location_query is not None:
        sets.append("pending_location_query = ?")
        vals.append(pending_location_query)
    if pending_location_choices is not None:
        sets.append("pending_location_choices = ?")
        vals.append(pending_location_choices)
    if not sets:
        return
    sets.append("updated_at = ?")
    vals.append(now_iso())
    vals.append(handle_id)

    con = db_connect()
    con.execute(f"UPDATE convo_state SET {', '.join(sets)} WHERE handle_id = ?", vals)
    con.commit()
    con.close()

def clear_pending_location(handle_id: str) -> None:
    set_convo_meta(handle_id, pending_location_query="", pending_location_choices="")

def display_first_name(handle_id: str) -> str:
    p = get_person(handle_id)
    if p.get("first_name"):
        return str(p["first_name"]).strip()
    cn = lookup_contact_name(handle_id)
    if cn:
        return cn.strip().split()[0]
    return "there"

# ------------ elapsed formatter ------------

def human_elapsed(seconds: int) -> str:
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

# ------------ NWS forecast ------------

def nws_forecast_one_liner(lat: float, lon: float) -> str:
    headers = {
        "User-Agent": NWS_USER_AGENT,
        "Accept": "application/geo+json, application/json",
    }
    points_url = f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}"
    p = requests.get(points_url, headers=headers, timeout=10)
    p.raise_for_status()
    pj = p.json()

    forecast_url = ((pj.get("properties") or {}).get("forecast"))
    if not forecast_url:
        raise ValueError("NWS points response missing properties.forecast")

    f = requests.get(forecast_url, headers=headers, timeout=10)
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
• help / ?                 Show this help
• weather / wx             Get your forecast (based on saved location)
• I'm in <place> now       Update your location and get forecast
• <City, ST> or <ZIP>      Set your location during setup
If I find multiple matches, I’ll ask you to reply with a number.
Examples:
• weather
• wx
• I'm in Davis, CA now
• 95616
• Paris, France
"""

WEATHER_COMMANDS = {"weather", "wx", "forecast", "temp"}

IN_NOW_RE = re.compile(
    r"""^\s*(?:i'?m|i\s+am)?\s*in\s+(?P<loc>.+?)\s+now\s*$""",
    re.IGNORECASE,
)

PICK_RE = re.compile(r"^\s*(?:use|pick|choose)?\s*(\d{1,2})\s*$", re.IGNORECASE)

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

def parse_pick_number(text: str) -> int | None:
    m = PICK_RE.match(text or "")
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None

# ------------ disambiguation UX ------------

def format_choice_list(query: str, choices: list[GeoCandidate]) -> str:
    lines = [f'I found multiple matches for “{query}”. Reply with a number:']
    for i, c in enumerate(choices[:5], start=1):
        lines.append(f"{i}) {c.label}")
    lines.append("")
    lines.append("Tip: You can also send City, ST (e.g., Davis, CA) or a ZIP code.")
    return "\n".join(lines)

def store_pending_choices(handle_id: str, query: str, choices: list[GeoCandidate]) -> None:
    blob = json.dumps([c.__dict__ for c in choices[:5]])
    set_convo_meta(handle_id, pending_location_query=query, pending_location_choices=blob)
    set_state(handle_id, "need_location_choice")

def load_pending_choices(handle_id: str) -> tuple[str, list[GeoCandidate]]:
    meta = get_convo_meta(handle_id)
    q = meta.get("pending_location_query") or ""
    raw = meta.get("pending_location_choices") or "[]"
    try:
        arr = json.loads(raw) if raw else []
    except Exception:
        arr = []
    out: list[GeoCandidate] = []
    for item in arr:
        try:
            out.append(GeoCandidate(label=item["label"], lat=float(item["lat"]), lon=float(item["lon"]), source=item.get("source", "unknown")))
        except Exception:
            continue
    return q, out

# ------------ conversation logic ------------

def set_location_direct(handle_id: str, label: str, lat: float, lon: float) -> None:
    update_person(handle_id, location_text=label, lat=lat, lon=lon)
    clear_pending_location(handle_id)
    set_state(handle_id, "ready")

def set_location_from_text_with_disambiguation(handle_id: str, raw_loc: str) -> tuple[float, float, str]:
    """
    Try to geocode. If ambiguous, store choices and prompt user.
    Returns (lat, lon, label) if set; raises AmbiguousLocation to trigger prompt.
    """
    try:
        cand = pick_best_or_raise(raw_loc)
        set_location_direct(handle_id, cand.label, cand.lat, cand.lon)
        return cand.lat, cand.lon, cand.label
    except AmbiguousLocation as amb:
        store_pending_choices(handle_id, amb.query, amb.candidates)
        raise
    except GeocodeError as e:
        raise
    except Exception as e:
        raise GeocodeError(str(e)) from e

def reply_weather(handle_id: str, loc: str, lat: float, lon: float) -> None:
    first = display_first_name(handle_id)
    greeting = time_of_day_greeting(datetime.now())
    try:
        wx = nws_forecast_one_liner(lat, lon)
    except Exception as e:
        wx = f"Weather lookup failed ({e})"
    send_imessage(handle_id, f"{greeting} Hello {first} — forecast for {loc}:\n\n{wx}")

def maybe_send_welcome_back(handle_id: str) -> None:
    meta = get_convo_meta(handle_id)
    last_incoming = parse_iso(meta.get("last_incoming_at") or "")
    last_welcome = parse_iso(meta.get("last_welcome_at") or "")

    if not last_incoming:
        return

    now = datetime.now(timezone.utc)
    gap = int((now - last_incoming).total_seconds())

    if gap < WELCOME_BACK_GAP_SECONDS:
        return

    if last_welcome and last_welcome > last_incoming:
        return

    first = display_first_name(handle_id)
    elapsed = human_elapsed(gap)
    send_imessage(handle_id, f"Welcome back, {first}. It’s been {elapsed} since you last texted.")
    set_convo_meta(handle_id, last_welcome_at=now_iso())

def handle_incoming(msg: Incoming) -> None:
    ensure_person_row(msg.handle_id)

    # Update last-seen times
    update_person(msg.handle_id, last_seen_at=now_iso())

    maybe_send_welcome_back(msg.handle_id)
    set_convo_meta(msg.handle_id, last_incoming_at=now_iso())

    if not msg.text:
        return

    # Global help
    if is_help(msg.text):
        send_imessage(msg.handle_id, HELP_TEXT)
        return

    # If we're waiting for a location choice, handle it first
    state = get_state(msg.handle_id)
    if state == "need_location_choice":
        pick = parse_pick_number(msg.text)
        q, choices = load_pending_choices(msg.handle_id)
        if pick is None or pick < 1 or pick > len(choices):
            # Let them also override by sending a new location text
            # Try treat message as new location input; if ambiguous again, it'll prompt.
            raw_try = normalize_text(msg.text)
            if raw_try:
                try:
                    lat, lon, label = set_location_from_text_with_disambiguation(msg.handle_id, raw_try)
                    send_imessage(msg.handle_id, f"Saved your location as: {label}. Text “weather” or “wx” anytime.")
                    return
                except AmbiguousLocation as amb:
                    send_imessage(msg.handle_id, format_choice_list(amb.query, amb.candidates))
                    return
                except Exception:
                    pass

            send_imessage(msg.handle_id, f"Please reply with a number 1–{max(1,len(choices))} (or send City, ST / ZIP).")
            return

        chosen = choices[pick - 1]
        set_location_direct(msg.handle_id, chosen.label, chosen.lat, chosen.lon)
        # If they were updating location via "I'm in ... now" we’ll just confirm + forecast on next command.
        send_imessage(msg.handle_id, f"Got it — saved your location as: {chosen.label}. Text “weather” or “wx” anytime.")
        return

    # "I'm in <location> now" always works
    in_now_loc = extract_in_now_location(msg.text)
    if in_now_loc:
        try:
            lat, lon, label = set_location_from_text_with_disambiguation(msg.handle_id, in_now_loc)
        except AmbiguousLocation as amb:
            send_imessage(msg.handle_id, format_choice_list(amb.query, amb.candidates))
            return
        except Exception as e:
            send_imessage(msg.handle_id, f"Sorry — I couldn’t find that location. Try: “Davis, CA” or a ZIP code. ({e})")
            return

        reply_weather(msg.handle_id, label, lat, lon)
        return

    # Onboarding state machine
    state = get_state(msg.handle_id)

    if state == "need_first":
        cn = lookup_contact_name(msg.handle_id)
        if cn:
            parts = cn.split()
            first = parts[0]
            last = " ".join(parts[1:]) if len(parts) > 1 else ""
            update_person(msg.handle_id, first_name=first, last_name=last)
            set_state(msg.handle_id, "need_location")
            send_imessage(msg.handle_id, f"Hi {first}! What city and state are you in? (e.g., Davis, CA — or send a ZIP)")
            return

        send_imessage(msg.handle_id, "Hi! What’s your first name?")
        set_state(msg.handle_id, "need_last")
        return

    if state == "need_last":
        p = get_person(msg.handle_id)
        if not p.get("first_name"):
            first = normalize_text(msg.text)
            update_person(msg.handle_id, first_name=first)
            send_imessage(msg.handle_id, f"Nice to meet you, {first}. What’s your last name?")
            return
        else:
            last = normalize_text(msg.text)
            update_person(msg.handle_id, last_name=last)
            set_state(msg.handle_id, "need_location")
            first = display_first_name(msg.handle_id)
            send_imessage(msg.handle_id, f"Thanks {first}! What city and state are you in? (e.g., Davis, CA — or send a ZIP)")
            return

    if state == "need_location":
        loc = normalize_text(msg.text)
        try:
            lat, lon, label = set_location_from_text_with_disambiguation(msg.handle_id, loc)
        except AmbiguousLocation as amb:
            send_imessage(msg.handle_id, format_choice_list(amb.query, amb.candidates))
            return
        except Exception as e:
            send_imessage(msg.handle_id, f"Sorry — I couldn’t find that location. Try: “Davis, CA” or a ZIP code. ({e})")
            return

        first = display_first_name(msg.handle_id)
        send_imessage(msg.handle_id, f"Thanks {first}! Saved your location as: {label}. Text “weather” or “wx” anytime.")
        return

    # ready state:
    if is_weather_cmd(msg.text):
        p = get_person(msg.handle_id)
        loc = p.get("location_text")
        lat = p.get("lat")
        lon = p.get("lon")
        if not loc or lat is None or lon is None:
            set_state(msg.handle_id, "need_location")
            send_imessage(msg.handle_id, f"What city and state are you in, {display_first_name(msg.handle_id)}? (e.g., Davis, CA — or send a ZIP)")
            return
        reply_weather(msg.handle_id, loc, float(lat), float(lon))
        return

    # Optional convenience: if user just sends a location while ready, treat it as location update
    # (This is surprisingly nice UX.)
    raw = normalize_text(msg.text)
    if raw and ("," in raw or raw.isdigit()):
        try:
            lat, lon, label = set_location_from_text_with_disambiguation(msg.handle_id, raw)
            send_imessage(msg.handle_id, f"Updated your location to: {label}. Text “weather” or “wx” anytime.")
            return
        except AmbiguousLocation as amb:
            send_imessage(msg.handle_id, format_choice_list(amb.query, amb.candidates))
            return
        except Exception:
            pass

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

