#!/usr/bin/env python3
import pathlib
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

STATE_FILE = pathlib.Path.home() / ".imessage_autoreply_last_rowid"
CHAT_DB = pathlib.Path.home() / "Library/Messages/chat.db"

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

def run_osascript(script: str, args: list[str]) -> str:
    p = subprocess.run(
        ["/usr/bin/osascript", "-l", "AppleScript", "-e", script, *args],
        text=True,
        capture_output=True,
    )
    if p.returncode != 0:
        raise RuntimeError((p.stderr or "").strip() or "osascript failed")
    return (p.stdout or "").strip()

def read_last_rowid() -> int:
    try:
        return int(STATE_FILE.read_text(encoding="utf-8").strip())
    except FileNotFoundError:
        return 0
    except ValueError:
        return 0

def write_last_rowid(rowid: int) -> None:
    STATE_FILE.write_text(str(rowid), encoding="utf-8")

def time_of_day_greeting(dt: datetime) -> str:
    h = dt.hour
    if 5 <= h < 12:
        return "Good morning"
    if 12 <= h < 17:
        return "Good afternoon"
    if 17 <= h < 23:
        return "Good evening"
    return "God it's late"

def build_reply_text() -> str:
    now_local = datetime.now()
    now_utc = datetime.now(timezone.utc)

    greeting = time_of_day_greeting(now_local)
    day_name = now_local.strftime("%A")
    month_name = now_local.strftime("%B")
    day_num = now_local.day
    year_num = now_local.year

    time_local = now_local.strftime("%I:%M:%S %p").lstrip("0")
    time_utc = now_utc.strftime("%H:%M:%S")

    return (
        f"{greeting}. It is now {day_name}, {month_name} {day_num}, {year_num} "
        f"at {time_local} (or {time_utc} UTC)."
    )

def get_latest_incoming_since(last_rowid: int) -> tuple[int, str] | None:
    if not CHAT_DB.exists():
        raise FileNotFoundError(f"Missing Messages DB: {CHAT_DB}")

    # Open read-only, no locking
    uri = f"file:{CHAT_DB}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row

    # Most recent *incoming* message (is_from_me = 0), with handle (email/phone)
    # handle.id is usually the address (e.g. pbertain@mac.com or +1...)
    row = con.execute(
        """
        SELECT
            message.ROWID AS rowid,
            handle.id AS handle_id
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

    return int(row["rowid"]), str(row["handle_id"])

def main() -> int:
    last_rowid = read_last_rowid()
    latest = get_latest_incoming_since(last_rowid)
    if latest is None:
        print("NO_NEW_INCOMING")
        return 0

    rowid, handle_id = latest
    reply_text = build_reply_text()

    res = run_osascript(SEND_SCRIPT, [handle_id, reply_text])
    if res != "OK":
        print(res, file=sys.stderr)
        return 1

    write_last_rowid(rowid)
    print(f"SENT:{rowid} to {handle_id}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

