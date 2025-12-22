#!/usr/bin/env python3
import pathlib
import sqlite3
import subprocess
import sys
import time
import logging
from datetime import datetime, timezone

import requests

STATE_FILE = pathlib.Path.home() / ".imessage_autoreply_last_rowid"
CHAT_DB = pathlib.Path.home() / "Library/Messages/chat.db"

LOG_PATH = pathlib.Path.home() / "imessage-autoreply.log"
POLL_SECONDS = 3
MAX_PER_POLL = 20  # safety: don't blast if you were offline for a while

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

logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


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
    except Exception:
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


def get_weather_davis() -> str:
    try:
        resp = requests.get("https://wttr.in/Davis,CA?format=3&u", timeout=5)
        resp.raise_for_status()
        return resp.text.strip()
    except Exception as e:
        logging.warning("Weather lookup failed: %s", e)
        return "Weather lookup failed"


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

    wx = get_weather_davis()

    return (
        f"{greeting}. It is now {day_name}, {month_name} {day_num}, {year_num} "
        f"at {time_local} (or {time_utc} UTC).\n\n"
        f"Davis weather: {wx}"
    )


def get_incoming_since(last_rowid: int, limit: int = MAX_PER_POLL) -> list[tuple[int, str]]:
    """
    Returns a list of (rowid, handle_id) for *incoming* messages newer than last_rowid,
    ordered oldest->newest so replies happen in sane order.
    """
    if not CHAT_DB.exists():
        raise FileNotFoundError(f"Missing Messages DB: {CHAT_DB}")

    uri = f"file:{CHAT_DB}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row

    rows = con.execute(
        """
        SELECT
            message.ROWID AS rowid,
            handle.id AS handle_id
        FROM message
        JOIN handle ON handle.ROWID = message.handle_id
        WHERE message.is_from_me = 0
          AND message.ROWID > ?
        ORDER BY message.ROWID ASC
        LIMIT ?
        """,
        (last_rowid, int(limit)),
    ).fetchall()

    con.close()

    out: list[tuple[int, str]] = []
    for r in rows:
        out.append((int(r["rowid"]), str(r["handle_id"])))
    return out


def main() -> int:
    logging.info("Starting iMessage autoreply daemon")
    last_rowid = read_last_rowid()
    logging.info("Initial last_rowid=%s", last_rowid)

    while True:
        try:
            msgs = get_incoming_since(last_rowid)
            if not msgs:
                time.sleep(POLL_SECONDS)
                continue

            # Build reply once per poll (or you can build per message if you want)
            reply_text = build_reply_text()

            for rowid, handle_id in msgs:
                res = run_osascript(SEND_SCRIPT, [handle_id, reply_text])
                if res != "OK":
                    logging.error("Send failed rowid=%s handle=%s res=%r", rowid, handle_id, res)
                    # don't advance state past a failed send
                    break

                last_rowid = rowid
                write_last_rowid(last_rowid)
                logging.info("Sent reply to %s (rowid=%s)", handle_id, rowid)

            time.sleep(POLL_SECONDS)

        except KeyboardInterrupt:
            logging.info("KeyboardInterrupt, exiting")
            return 0
        except Exception as e:
            logging.exception("Loop error: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())

