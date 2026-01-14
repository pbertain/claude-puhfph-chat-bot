#!/usr/bin/env python3
"""
Database operations for profile and conversation state management.
"""
import sqlite3
import time
from datetime import datetime, timezone
from typing import Optional

import config


def db_connect() -> sqlite3.Connection:
    """Connect to the profile database with appropriate settings."""
    # timeout helps with "database is locked"
    # Increase timeout and add retries for locked database
    max_retries = 3
    for attempt in range(max_retries):
        try:
            con = sqlite3.connect(config.PROFILE_DB, timeout=10.0)
            con.execute("PRAGMA journal_mode=WAL;")
            con.execute("PRAGMA foreign_keys=ON;")
            con.execute("PRAGMA busy_timeout=10000;")  # 10 second busy timeout
            return con
        except sqlite3.OperationalError as e:
            if "unable to open database file" in str(e).lower():
                raise PermissionError(
                    f"Cannot create/access profile database: {config.PROFILE_DB}\n"
                    "Check that the directory exists and you have write permissions."
                ) from e
            if "locked" in str(e).lower() and attempt < max_retries - 1:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise


def db_exec(fn, *, retries: int = 5, delay: float = 0.15):
    """
    Small retry wrapper for transient SQLITE_BUSY/locked errors.
    """
    last_err = None
    for i in range(retries):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "locked" in msg or "busy" in msg:
                last_err = e
                time.sleep(delay * (i + 1))
                continue
            raise
    raise last_err or RuntimeError("DB operation failed")


def now_iso() -> str:
    """Get current UTC time as ISO format string."""
    return datetime.now(timezone.utc).isoformat()


def parse_iso(ts: str) -> Optional[datetime]:
    """Parse an ISO format timestamp string."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def db_init() -> None:
    """Initialize the database schema."""
    def _init():
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
              state TEXT NOT NULL,              -- 'need_first' | 'need_last' | 'need_location' | 'ready' | 'need_alarm_time' | 'need_alarm_message' | 'need_alarm_repeat'
              last_incoming_at TEXT,
              last_welcome_at TEXT,
              temp_data TEXT,                   -- JSON for temporary data (e.g., alarm creation)
              updated_at TEXT NOT NULL,
              FOREIGN KEY(handle_id) REFERENCES person(handle_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS scheduled_messages (
              schedule_id INTEGER PRIMARY KEY AUTOINCREMENT,
              handle_id TEXT NOT NULL,
              message_type TEXT NOT NULL,        -- 'weather', 'alarm', 'reminder', 'metar'
              message_payload TEXT,              -- optional payload (e.g., station ids)
              schedule_time TEXT,                -- HH:MM:SS format (NULL for relative time schedules)
              schedule_type TEXT NOT NULL,       -- 'daily' | 'once'
              next_run_at TEXT NOT NULL,         -- ISO format timestamp
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(handle_id) REFERENCES person(handle_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_scheduled_messages_next_run 
            ON scheduled_messages(next_run_at);

            CREATE TABLE IF NOT EXISTS alarms (
              alarm_id INTEGER PRIMARY KEY AUTOINCREMENT,
              handle_id TEXT NOT NULL,
              alarm_title TEXT NOT NULL,
              alert_time TEXT NOT NULL,          -- HH:MM:SS format
              alert_message TEXT NOT NULL,
              schedule_type TEXT NOT NULL,       -- 'daily' | 'once'
              next_run_at TEXT NOT NULL,         -- ISO format timestamp
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(handle_id) REFERENCES person(handle_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_alarms_next_run 
            ON alarms(next_run_at);
            """
        )
        
        # Check if convo_state table needs temp_data column
        try:
            cursor = con.execute("PRAGMA table_info(convo_state)")
            columns = [row[1] for row in cursor.fetchall()]
            if "temp_data" not in columns:
                con.execute("ALTER TABLE convo_state ADD COLUMN temp_data TEXT")
                con.commit()
        except Exception:
            pass
        
        # Check if alarms table exists, create if not
        try:
            cursor = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='alarms'")
            if not cursor.fetchone():
                con.execute(
                    """
                    CREATE TABLE alarms (
                      alarm_id INTEGER PRIMARY KEY AUTOINCREMENT,
                      handle_id TEXT NOT NULL,
                      alarm_title TEXT NOT NULL,
                      alert_time TEXT NOT NULL,
                      alert_message TEXT NOT NULL,
                      schedule_type TEXT NOT NULL,
                      next_run_at TEXT NOT NULL,
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL,
                      FOREIGN KEY(handle_id) REFERENCES person(handle_id) ON DELETE CASCADE
                    );
                    CREATE INDEX IF NOT EXISTS idx_alarms_next_run ON alarms(next_run_at);
                    """
                )
                con.commit()
        except Exception:
            pass
        
        # Check if table exists with old schema (schedule_time NOT NULL) and migrate if needed
        try:
            cursor = con.execute("PRAGMA table_info(scheduled_messages)")
            columns = cursor.fetchall()
            # Find schedule_time column - check if it's NOT NULL
            schedule_time_col = next((col for col in columns if col[1] == "schedule_time"), None)
            if schedule_time_col and schedule_time_col[3] == 1:  # 1 means NOT NULL constraint
                # Need to migrate - recreate table
                con.execute("BEGIN TRANSACTION")
                try:
                    con.execute("""
                        CREATE TABLE scheduled_messages_new (
                          schedule_id INTEGER PRIMARY KEY AUTOINCREMENT,
                          handle_id TEXT NOT NULL,
                          message_type TEXT NOT NULL,
                          message_payload TEXT,
                          schedule_time TEXT,
                          schedule_type TEXT NOT NULL,
                          next_run_at TEXT NOT NULL,
                          created_at TEXT NOT NULL,
                          updated_at TEXT NOT NULL,
                          FOREIGN KEY(handle_id) REFERENCES person(handle_id) ON DELETE CASCADE
                        )
                    """)
                    con.execute("""
                        INSERT INTO scheduled_messages_new 
                        SELECT schedule_id, handle_id, message_type, NULL, schedule_time, schedule_type, next_run_at, created_at, updated_at
                        FROM scheduled_messages
                    """)
                    con.execute("DROP TABLE scheduled_messages")
                    con.execute("ALTER TABLE scheduled_messages_new RENAME TO scheduled_messages")
                    con.execute("""
                        CREATE INDEX IF NOT EXISTS idx_scheduled_messages_next_run 
                        ON scheduled_messages(next_run_at)
                    """)
                    con.commit()
                except Exception:
                    con.rollback()
                    raise
        except sqlite3.OperationalError:
            # Table doesn't exist yet, that's fine
            pass

        # Add message_payload column if missing
        try:
            cursor = con.execute("PRAGMA table_info(scheduled_messages)")
            columns = cursor.fetchall()
            has_payload = any(col[1] == "message_payload" for col in columns)
            if not has_payload:
                con.execute("ALTER TABLE scheduled_messages ADD COLUMN message_payload TEXT")
        except sqlite3.OperationalError:
            pass
        
        con.commit()
        con.close()
    db_exec(_init)


def ensure_person_row(handle_id: str) -> None:
    """Ensure a person row exists, creating it if necessary."""
    ts = now_iso()

    def _do():
        con = db_connect()
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
            INSERT INTO convo_state(handle_id, state, last_incoming_at, last_welcome_at, updated_at)
            VALUES(?, 'need_first', NULL, NULL, ?)
            ON CONFLICT(handle_id) DO NOTHING
            """,
            (handle_id, ts),
        )

        con.commit()
        con.close()

    db_exec(_do)


def get_state(handle_id: str) -> str:
    """Get the conversation state for a handle."""
    def _do():
        con = db_connect()
        row = con.execute(
            "SELECT state FROM convo_state WHERE handle_id = ?",
            (handle_id,),
        ).fetchone()
        con.close()
        return row[0] if row else "need_first"

    return db_exec(_do)


def get_temp_data(handle_id: str) -> dict:
    """Get temporary data (JSON) for a handle."""
    import json
    def _do():
        con = db_connect()
        row = con.execute("SELECT temp_data FROM convo_state WHERE handle_id = ?", (handle_id,)).fetchone()
        con.close()
        if row and row[0]:
            try:
                return json.loads(row[0])
            except Exception:
                return {}
        return {}
    return db_exec(_do)


def set_temp_data(handle_id: str, data: dict) -> None:
    """Set temporary data (JSON) for a handle."""
    import json
    def _do():
        con = db_connect()
        con.execute(
            "UPDATE convo_state SET temp_data = ?, updated_at = ? WHERE handle_id = ?",
            (json.dumps(data), now_iso(), handle_id),
        )
        con.commit()
        con.close()
    db_exec(_do)


def set_state(handle_id: str, state: str) -> None:
    """Set the conversation state for a handle."""
    def _do():
        con = db_connect()
        con.execute(
            "UPDATE convo_state SET state = ?, updated_at = ? WHERE handle_id = ?",
            (state, now_iso(), handle_id),
        )
        con.commit()
        con.close()
    db_exec(_do)


def update_person(handle_id: str, **fields) -> None:
    """Update person fields. Fields can be: first_name, last_name, location_text, lat, lon, last_seen_at."""
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

    def _do():
        con = db_connect()
        con.execute(f"UPDATE person SET {', '.join(cols)} WHERE handle_id = ?", vals)
        con.commit()
        con.close()
    db_exec(_do)


def get_person(handle_id: str) -> dict:
    """Get person data for a handle."""
    def _do():
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

    return db_exec(_do)


def get_convo_meta(handle_id: str) -> dict:
    """Get conversation metadata (timestamps)."""
    def _do():
        con = db_connect()
        row = con.execute(
            "SELECT last_incoming_at, last_welcome_at FROM convo_state WHERE handle_id = ?",
            (handle_id,),
        ).fetchone()
        con.close()
        return {
            "last_incoming_at": row[0] if row else None,
            "last_welcome_at": row[1] if row else None,
        }
    return db_exec(_do)


def set_convo_meta(handle_id: str, *, last_incoming_at: Optional[str] = None, last_welcome_at: Optional[str] = None) -> None:
    """Update conversation metadata timestamps."""
    sets = []
    vals: list[str] = []
    if last_incoming_at is not None:
        sets.append("last_incoming_at = ?")
        vals.append(last_incoming_at)
    if last_welcome_at is not None:
        sets.append("last_welcome_at = ?")
        vals.append(last_welcome_at)
    if not sets:
        return
    sets.append("updated_at = ?")
    vals.append(now_iso())
    vals.append(handle_id)

    def _do():
        con = db_connect()
        con.execute(f"UPDATE convo_state SET {', '.join(sets)} WHERE handle_id = ?", vals)
        con.commit()
        con.close()
    db_exec(_do)


def create_alarm(handle_id: str, alarm_title: str, alert_time: str, alert_message: str, 
                 schedule_type: str, next_run_at: str) -> int:
    """Create an alarm in the database. Returns alarm_id."""
    def _do():
        con = db_connect()
        cursor = con.execute(
            """
            INSERT INTO alarms 
            (handle_id, alarm_title, alert_time, alert_message, schedule_type, next_run_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                handle_id,
                alarm_title,
                alert_time,
                alert_message,
                schedule_type,
                next_run_at,
                now_iso(),
                now_iso(),
            ),
        )
        alarm_id = cursor.lastrowid
        con.commit()
        con.close()
        return alarm_id
    return db_exec(_do)


def get_due_alarms(now: str) -> list[dict]:
    """Get all alarms that are due to run. Immediately updates their next_run_at to prevent re-selection."""
    def _do():
        con = db_connect()
        
        # Select due alarms
        rows = con.execute(
            """
            SELECT alarm_id, handle_id, alarm_title, alert_time, alert_message, schedule_type, next_run_at
            FROM alarms
            WHERE next_run_at <= ?
            ORDER BY next_run_at ASC
            """,
            (now,),
        ).fetchall()
        
        if not rows:
            con.close()
            return []

        alarm_ids = [row[0] for row in rows]
        
        # Immediately update next_run_at to a far-future temporary marker
        # This prevents the same alarms from being picked up again in the same poll loop
        from datetime import timedelta
        temp_marker = (datetime.now(timezone.utc) + timedelta(days=365 * 10)).isoformat()  # 10 years in the future
        con.execute(
            f"""
            UPDATE alarms
            SET next_run_at = ?, updated_at = ?
            WHERE alarm_id IN ({','.join('?' * len(alarm_ids))})
            """,
            (temp_marker, now_iso(), *alarm_ids),
        )
        con.commit()
        con.close()
        
        return [
            {
                "alarm_id": row[0],
                "handle_id": row[1],
                "alarm_title": row[2],
                "alert_time": row[3],
                "alert_message": row[4],
                "schedule_type": row[5],
                "next_run_at": row[6],
            }
            for row in rows
        ]
    return db_exec(_do)


def update_alarm_next_run(alarm_id: int, next_run_at: str) -> None:
    """Update the next_run_at for an alarm."""
    def _do():
        con = db_connect()
        con.execute(
            "UPDATE alarms SET next_run_at = ?, updated_at = ? WHERE alarm_id = ?",
            (next_run_at, now_iso(), alarm_id),
        )
        con.commit()
        con.close()
    db_exec(_do)


def delete_alarm(alarm_id: int) -> None:
    """Delete an alarm."""
    def _do():
        con = db_connect()
        con.execute("DELETE FROM alarms WHERE alarm_id = ?", (alarm_id,))
        con.commit()
        con.close()
    db_exec(_do)

