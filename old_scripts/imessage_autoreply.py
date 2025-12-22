#!/usr/bin/env python3
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

STATE_FILE = pathlib.Path.home() / ".imessage_autoreply_last_id"

POLL_SCRIPT = r'''
on run argv
	tell application "Messages"
		set targetService to first service whose service type = iMessage
		set allChats to chats of targetService
		if (count of allChats) = 0 then return "NOCHAT"

		set newestMsg to missing value
		set newestChat to missing value
		set newestDate to date "January 1, 1900 12:00:00 AM"

		repeat with c in allChats
			set msgs to messages of c
			if (count of msgs) > 0 then
				set m to last item of msgs
				try
					set d to date sent of m
				on error
					set d to current date
				end try

				if d > newestDate then
					set newestDate to d
					set newestMsg to m
					set newestChat to c
				end if
			end if
		end repeat

		if newestMsg is missing value then return "NOMSG"

		set msgID to (id of newestMsg) as string
		set chatID to (id of newestChat) as string
		return msgID & "|" & chatID
	end tell
end run
'''

SEND_SCRIPT = r'''
on run argv
	if (count of argv) < 2 then return "ERR:ARGS"
	set chatID to item 1 of argv
	set replyText to item 2 of argv

	tell application "Messages"
		set targetService to first service whose service type = iMessage
		set theChat to missing value

		repeat with c in (chats of targetService)
			if ((id of c) as string) = (chatID as string) then
				set theChat to c
				exit repeat
			end if
		end repeat

		if theChat is missing value then return "ERR:NOCHATID"
		send replyText to theChat
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

def read_last_id() -> str:
    try:
        return STATE_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""

def write_last_id(msg_id: str) -> None:
    STATE_FILE.write_text(msg_id, encoding="utf-8")

def build_reply_text() -> str:
    now_local = datetime.now()
    now_utc = datetime.now(timezone.utc)

    h = now_local.hour
    if 5 <= h < 12:
        greeting = "Good morning"
    elif 12 <= h < 17:
        greeting = "Good afternoon"
    elif 17 <= h < 23:
        greeting = "Good evening"
    else:
        greeting = "God it's late"

    day_name = now_local.strftime("%A")
    month_name = now_local.strftime("%B")
    day_num = now_local.day
    year_num = now_local.year

    # nice local time like "3:42:10 PM"
    time_local = now_local.strftime("%I:%M:%S %p").lstrip("0")
    # UTC like "23:42:10"
    time_utc = now_utc.strftime("%H:%M:%S")

    return f"{greeting}. It is now {day_name}, {month_name} {day_num}, {year_num} at {time_local} (or {time_utc} UTC)."

def main() -> int:
    last_id = read_last_id()

    poll = run_osascript(POLL_SCRIPT, [])
    if poll in ("NOCHAT", "NOMSG"):
        print(poll)
        return 0

    msg_id, chat_id = poll.split("|", 1)

    if msg_id == last_id:
        print("DUP")
        return 0

    reply_text = build_reply_text()
    send_res = run_osascript(SEND_SCRIPT, [chat_id, reply_text])
    if send_res != "OK":
        print(send_res, file=sys.stderr)
        return 1

    write_last_id(msg_id)
    print(f"SENT:{msg_id}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

