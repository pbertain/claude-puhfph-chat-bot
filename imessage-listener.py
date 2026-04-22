#!/usr/bin/env python3
"""
Main iMessage listener bot - polls for messages and handles them.
"""
import sys
import time

import config
import conversation
import database
import message_polling
import scheduler


def preflight_check() -> bool:
    """
    Check macOS permissions before starting the main loop.
    Prints clear guidance and returns False if any critical permission is missing.
    """
    import subprocess
    ok = True

    # 1. Full Disk Access — can we read chat.db?
    if not config.CHAT_DB.exists():
        print(
            f"ERROR: Messages database not found: {config.CHAT_DB}\n"
            "Make sure Messages.app has been opened at least once.",
            file=sys.stderr,
        )
        ok = False
    else:
        try:
            import sqlite3
            con = sqlite3.connect(f"file:{config.CHAT_DB}?mode=ro", uri=True)
            con.execute("SELECT 1")
            con.close()
        except Exception as e:
            print(
                f"ERROR: Cannot read Messages database: {e}\n"
                "Fix: System Settings > Privacy & Security > Full Disk Access\n"
                f"     Add this Python: {sys.executable}",
                file=sys.stderr,
            )
            ok = False

    # 2. Automation — can we send Apple Events to Messages?
    p = subprocess.run(
        ["/usr/bin/osascript", "-l", "AppleScript", "-e",
         'tell application "Messages" to get name'],
        text=True,
        capture_output=True,
    )
    if p.returncode != 0:
        print(
            f"ERROR: Cannot control Messages.app via AppleScript: {p.stderr.strip()}\n"
            "Fix: System Settings > Privacy & Security > Automation\n"
            f"     Allow this Python ({sys.executable}) to control Messages.",
            file=sys.stderr,
        )
        ok = False

    # 3. Automation — can we send Apple Events to Contacts?
    p = subprocess.run(
        ["/usr/bin/osascript", "-l", "AppleScript", "-e",
         'tell application "Contacts" to get name'],
        text=True,
        capture_output=True,
    )
    if p.returncode != 0:
        print(
            f"ERROR: Cannot control Contacts.app via AppleScript: {p.stderr.strip()}\n"
            "Fix: System Settings > Privacy & Security > Automation\n"
            f"     Allow this Python ({sys.executable}) to control Contacts.",
            file=sys.stderr,
        )
        # Contacts is non-fatal — bot can run without contact name lookups
        print("WARNING: Continuing without Contacts access (names won't be looked up).", file=sys.stderr)

    return ok


def main() -> int:
    """Main event loop."""
    if not preflight_check():
        print("Pre-flight check failed. Fix the errors above and restart.", file=sys.stderr)
        return 1

    database.db_init()
    last_rowid = message_polling.read_last_rowid()
    print("iMessage bot running. Ctrl-C to stop.")

    while True:
        try:
            # Check for scheduled messages that are due
            due_schedules = scheduler.get_due_scheduled_messages()
            for schedule in due_schedules:
                try:
                    if schedule["message_type"] == "weather":
                        conversation.execute_scheduled_weather(schedule["handle_id"])
                    elif schedule["message_type"] == "metar":
                        conversation.execute_scheduled_metar(
                            schedule["handle_id"],
                            schedule.get("message_payload") or "",
                        )
                    
                    # Update next run time (or delete if one-time)
                    scheduler.update_next_run(
                        schedule["schedule_id"],
                        schedule["schedule_time"],
                        schedule["schedule_type"],
                    )
                except Exception as e:
                    print(f"ERROR executing schedule {schedule['schedule_id']}: {e}", file=sys.stderr)
            
            # Check for alarms that are due
            now_iso = database.now_iso()
            due_alarms = database.get_due_alarms(now_iso)
            for alarm in due_alarms:
                try:
                    conversation.execute_alarm(alarm)
                except Exception as e:
                    print(f"ERROR executing alarm {alarm['alarm_id']}: {e}", file=sys.stderr)
            
            # Check for new incoming messages
            inc = message_polling.get_latest_incoming_since(last_rowid)
            if inc is None:
                time.sleep(config.POLL_SECONDS)
                continue

            last_rowid = inc.rowid
            message_polling.write_last_rowid(last_rowid)

            conversation.handle_incoming(inc)
            time.sleep(config.POLL_SECONDS)

        except KeyboardInterrupt:
            print("\nbye")
            return 0
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
