#!/usr/bin/env python3
"""
AppleScript helpers for iMessage and Contacts integration.
"""
import subprocess

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

# Returns best match; may be empty string.
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
    """Execute an AppleScript with the given arguments."""
    p = subprocess.run(
        ["/usr/bin/osascript", "-l", "AppleScript", "-e", script, *args],
        text=True,
        capture_output=True,
    )
    if p.returncode != 0:
        raise RuntimeError((p.stderr or "").strip() or "osascript failed")
    return (p.stdout or "").strip()


def send_imessage(handle_id: str, text: str) -> None:
    """Send an iMessage to the given handle."""
    res = run_osascript(SEND_SCRIPT, [handle_id, text])
    if res != "OK":
        raise RuntimeError(f"Messages send failed: {res}")


def lookup_contact_name(handle_id: str) -> str:
    """Look up a contact name by handle (email or phone). Returns empty string if not found."""
    try:
        return run_osascript(CONTACT_NAME_SCRIPT, [handle_id]).strip()
    except Exception:
        return ""

