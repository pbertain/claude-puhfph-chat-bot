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


def main() -> int:
    """Main event loop."""
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
