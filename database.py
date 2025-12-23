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
    try:
        con = sqlite3.connect(config.PROFILE_DB, timeout=5.0)
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA foreign_keys=ON;")
        return con
    except sqlite3.OperationalError as e:
        if "unable to open database file" in str(e).lower():
            raise PermissionError(
                f"Cannot create/access profile database: {config.PROFILE_DB}\n"
                "Check that the directory exists and you have write permissions."
            ) from e
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
              state TEXT NOT NULL,              -- 'need_first' | 'need_last' | 'need_location' | 'ready'
              last_incoming_at TEXT,
              last_welcome_at TEXT,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(handle_id) REFERENCES person(handle_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS scheduled_messages (
              schedule_id INTEGER PRIMARY KEY AUTOINCREMENT,
              handle_id TEXT NOT NULL,
              message_type TEXT NOT NULL,        -- 'weather', etc.
              schedule_time TEXT NOT NULL,       -- HH:MM:SS format
              schedule_type TEXT NOT NULL,       -- 'daily' | 'once'
              next_run_at TEXT NOT NULL,         -- ISO format timestamp
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(handle_id) REFERENCES person(handle_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_scheduled_messages_next_run 
            ON scheduled_messages(next_run_at);
            """
        )
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


def set_convo_meta(handle_id: str, *, last_incoming_at: str | None = None, last_welcome_at: str | None = None) -> None:
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

